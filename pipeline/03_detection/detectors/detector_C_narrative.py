"""
Detector C — Narrative Reuse (bipartite NER claim graph)
==========================================================
Catches "different people, same fabricated story" — i.e., unrelated organizers
all citing the same specific named entities (a fabricated hospital, a fake
victim, an invented child name, the same named tragedy).

Graph shape: BIPARTITE
  Node types:  campaigns + entities (PERSON, ORG, GPE, MONEY, DATE, ...)
  Edges:       campaign -> entity (campaign mentions entity)
  Why bipartite: entities are FIRST-CLASS. We need to:
    - compute IDF per entity (rare entities matter more)
    - audit which specific entity triggered each match
    - explain the verdict to a reviewer

Algorithm:
  1. Build campaign -> entity bipartite graph (entities tagged by type)
  2. Compute IDF per entity: idf(e) = log(N / N_campaigns_mentioning_e)
  3. Cap high-share entities (>50 campaigns) — they're generic, not signal
  4. For each pair of campaigns sharing >=N entities:
       score(c1, c2) = sum_of_IDF(shared_entities) + alpha * sbert_cosine
  5. Keep pairs with score >= THRESHOLD
  6. Connect surviving pairs into narrative_clusters via connected components
  7. Flag every campaign in a narrative_cluster of size >=2

Detectors are independent: a campaign flagged by both C and D simply
counts as two pieces of evidence in the consensus aggregator. No
exclusion gate between C and D.

Entity sources:
  • Llama-8B contact extraction: names + locations (REQUIRED — already have)
  • spaCy transformer NER: ORG, MONEY, DATE, GPE, EVENT, FAC, ... (OPTIONAL —
    enriches entity inventory; script gracefully skips if entities.jsonl
    is missing)

Inputs:
  ../../02_data_filtration/filtered_dataset.csv
  ../intel/campaign_contacts_llm.jsonl
  ../intel/entities.jsonl                  (optional — from spaCy NER GPU job)
  ../features/sbert_embeddings.npy         (one row per URL in sbert_url_order)
  ../features/sbert_url_order.csv

Output:
  outputs/detector_C_flags.csv
    url, flag, narrative_cluster_id, narrative_cluster_size, shared_entities_count, fired_signals
"""
from __future__ import annotations
import csv
import json
import math
import re
import sys
from collections import defaultdict, Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "02_data_filtration" / "filtered_dataset.csv"
INTEL = ROOT / "03_detection" / "intel"
FEATURES = ROOT / "03_detection" / "features"
CONTACTS  = INTEL / "campaign_contacts_llm.jsonl"
ENTITIES  = INTEL / "entities.jsonl"
SBERT_NPY = FEATURES / "sbert_embeddings.npy"
SBERT_CSV = FEATURES / "sbert_url_order.csv"
OUT = ROOT / "03_detection" / "detectors" / "outputs" / "detector_C_flags.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

MIN_SHARED_ENTITIES   = 3       # require >=3 shared entities (was 2; cuts noise)
ENTITY_SHARE_CAP      = 50      # entities appearing in >this many campaigns are dropped
SCORE_THRESHOLD       = 15.0    # IDF sum threshold to keep a pair (was 4.0)
SBERT_WEIGHT          = 2.0     # how much to weight the SBERT cosine bonus
MIN_ENTITY_LEN        = 3       # drop 1-2 char "entities"


GENERIC_ENTITIES = {
    # Names that are too common to be identity signals
    "god", "jesus", "allah", "lord", "mom", "dad", "mother", "father",
    "family", "friend", "friends", "neighbor", "doctor", "nurse",
    # Generic places
    "usa", "us", "america", "earth", "world", "europe", "asia",
    "north america", "south america",
    # Generic dates / numbers
    "today", "tomorrow", "yesterday", "now", "soon", "later",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    # Punctuation / artifact
    "the", "and", "or", "etc",
}


def normalize_entity(s):
    if not isinstance(s, str): return None
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    if len(s) < MIN_ENTITY_LEN: return None
    if s in GENERIC_ENTITIES: return None
    if re.match(r"^[\d\W]+$", s): return None   # pure digits/punct
    return s


def load_sbert():
    """url -> embedding row index, plus the numpy array."""
    if not SBERT_NPY.exists() or not SBERT_CSV.exists():
        print("  [info] SBERT files missing — narrative-similarity boost skipped")
        return None, None
    emb = np.load(SBERT_NPY)
    urls = pd.read_csv(SBERT_CSV)["url"].astype(str).tolist()
    return {u: i for i, u in enumerate(urls)}, emb


def main():
    print("Detector C — Narrative Reuse (bipartite NER + IDF + SBERT)")
    df = pd.read_csv(DATA, low_memory=False)
    print(f"  campaigns:               {len(df):,}")

    # Step 1: collect per-campaign entities
    campaign_entities = defaultdict(set)
    n_llm = n_spacy = 0
    if CONTACTS.exists():
        with CONTACTS.open() as fh:
            for line in fh:
                try: d = json.loads(line)
                except: continue
                if not d.get("ok"): continue
                url = d.get("url")
                if not url: continue
                for e in (d.get("names") or []):
                    norm = normalize_entity(e)
                    if norm: campaign_entities[url].add(("PERSON", norm)); n_llm += 1
                for e in (d.get("locations") or []):
                    norm = normalize_entity(e)
                    if norm: campaign_entities[url].add(("GPE", norm)); n_llm += 1
        print(f"  Llama entities loaded:   {n_llm:,}")
    else:
        print("  [warn] campaign_contacts_llm.jsonl missing — entities very sparse")

    if ENTITIES.exists():
        with ENTITIES.open() as fh:
            for line in fh:
                try: d = json.loads(line)
                except: continue
                url = d.get("url")
                if not url: continue
                ents = d.get("entities") or {}
                for typ, lst in ents.items():
                    for e in lst:
                        norm = normalize_entity(e)
                        if norm: campaign_entities[url].add((typ, norm)); n_spacy += 1
        print(f"  spaCy entities loaded:   {n_spacy:,}")
    else:
        print("  [info] entities.jsonl missing — running with Llama entities only")

    print(f"  campaigns with entities: {len(campaign_entities):,}")

    # Step 2: invert (entity -> campaigns)
    entity_campaigns = defaultdict(set)
    for url, ents in campaign_entities.items():
        for e in ents:
            entity_campaigns[e].add(url)
    print(f"  unique entities:         {len(entity_campaigns):,}")

    # Step 3: cap high-share entities + compute IDF
    N = len(df)
    high_share = [e for e, urls in entity_campaigns.items() if len(urls) > ENTITY_SHARE_CAP]
    print(f"  high-share entities (>{ENTITY_SHARE_CAP} campaigns): {len(high_share):,}")
    for e in high_share:
        del entity_campaigns[e]
    # Also drop entities mentioned in <2 campaigns (can't link)
    entity_campaigns = {e: urls for e, urls in entity_campaigns.items() if len(urls) >= 2}
    print(f"  entities used for linking: {len(entity_campaigns):,}")

    idf = {e: math.log(N / len(urls)) for e, urls in entity_campaigns.items()}

    # Rebuild campaign_entities to keep only "linking" entities
    campaign_entities = defaultdict(set)
    for e, urls in entity_campaigns.items():
        for u in urls:
            campaign_entities[u].add(e)

    # Step 4: SBERT lookup
    sbert_idx, sbert_emb = load_sbert()

    # Step 6: candidate pair generation via inverted index
    pair_shared = defaultdict(int)        # (u1,u2) sorted -> count
    pair_idf    = defaultdict(float)
    print(f"  generating candidate pairs...", flush=True)
    for e, urls in entity_campaigns.items():
        urls_l = sorted(urls)
        w = idf[e]
        for i in range(len(urls_l)):
            for j in range(i+1, len(urls_l)):
                key = (urls_l[i], urls_l[j])
                pair_shared[key] += 1
                pair_idf[key] += w
    print(f"  candidate pairs:         {len(pair_shared):,}")

    # Step 5: filter pairs by shared >= MIN_SHARED_ENTITIES,
    # add SBERT cosine bonus, then keep pairs with score >= THRESHOLD
    kept_pairs = []
    n_score_dropped = 0
    for key, shared in pair_shared.items():
        if shared < MIN_SHARED_ENTITIES:
            continue
        u1, u2 = key
        score = pair_idf[key]
        if sbert_idx is not None and sbert_emb is not None:
            i1, i2 = sbert_idx.get(u1), sbert_idx.get(u2)
            if i1 is not None and i2 is not None:
                cos = float(np.dot(sbert_emb[i1], sbert_emb[i2]))
                score += SBERT_WEIGHT * max(cos, 0)
        if score < SCORE_THRESHOLD:
            n_score_dropped += 1
            continue
        kept_pairs.append((u1, u2, shared, score))
    print(f"  pairs below score threshold:    {n_score_dropped:,}")
    print(f"  pairs kept:                     {len(kept_pairs):,}")

    # Step 8: connected components → story narrative_clusters
    import networkx as nx
    G = nx.Graph()
    for u1, u2, sh, sc in kept_pairs:
        G.add_edge(u1, u2, shared=sh, score=sc)
    narrative_clusters = list(nx.connected_components(G))
    print(f"  story narrative_clusters found:    {len(narrative_clusters):,}")
    if narrative_clusters:
        sizes = [len(f) for f in narrative_clusters]
        print(f"  family sizes:            max={max(sizes)}  median={sorted(sizes)[len(sizes)//2]}")

    url_to_cluster = {}
    narrative_cluster_sizes = {}
    for i, fam in enumerate(narrative_clusters, 1):
        narrative_cluster_sizes[i] = len(fam)
        for u in fam: url_to_cluster[u] = i

    # Step 9: write output
    n_flag = 0
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url","flag","narrative_cluster_id","narrative_cluster_size","shared_entities_count","fired_signals"])
        for u in df["url"]:
            cid = url_to_cluster.get(u)
            if cid:
                # gather max-shared count across this campaign's edges
                if u in G:
                    shared_max = max(G[u][v]["shared"] for v in G.neighbors(u))
                    score_max  = max(G[u][v]["score"] for v in G.neighbors(u))
                else:
                    shared_max = score_max = 0
                signals = f"shared={shared_max};score={score_max:.2f}"
                w.writerow([u, 1, cid, narrative_cluster_sizes[cid], shared_max, signals])
                n_flag += 1
            else:
                w.writerow([u, 0, "", 0, 0, ""])

    pct = 100*n_flag/len(df) if len(df) else 0
    print(f"\nflagged by Detector C:    {n_flag:,}  ({pct:.2f}%)")
    print(f"output: {OUT}")


if __name__ == "__main__":
    main()
