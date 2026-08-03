"""Shared MongoDB connection helper for the CCS2026 pipeline.

All loaders / detectors should call get_db() rather than constructing their
own MongoClient. This keeps the connection string in exactly one place.

The bacharlab mongod is on 127.0.0.1:27017 with no auth (bindIp restricts to
localhost + lab subnet). Override with MONGO_URI env var if running off-host.
"""
import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("MONGO_DB", "donationscam")


def get_client():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)


def get_db():
    return get_client()[DB_NAME]
