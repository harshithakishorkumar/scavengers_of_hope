from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os
INTER="/usr/share/fonts/opentype/inter/"
def font(n,s):
    p=INTER+n+".otf"; return ImageFont.truetype(p,s) if os.path.exists(p) else ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",s)
bold=lambda s:font("Inter-Bold",s); semi=lambda s:font("Inter-SemiBold",s)
reg=lambda s:font("Inter-Regular",s)
# light theme palette
INK=(28,33,48); SUB=(122,129,145); LINE=(226,229,236); PANELBD=(214,218,228)
LABELG=(140,146,160); PILLBG=(238,240,245); CARDBD=(226,229,236)
CARDBG=(248,249,251); PANELBG=(255,255,255)
DK=(70,77,95); CLR=(176,181,193); EVf=(70,77,95); EVc=(165,170,182)
FIRED_BG=(229,72,97); CLEAR_GRN=(16,158,112)
PW=1500
def tl(d,t,f): return d.textlength(t,font=f)
def ctext(d,cx,cy,t,f,fill):
    w=tl(d,t,f); a=f.getmetrics(); d.text((cx-w/2,cy-(a[0]+a[1])/2),t,font=f,fill=fill)
def g_shield(d,cx,cy,c): d.polygon([(cx-28,cy-32),(cx+28,cy-32),(cx+28,cy+4),(cx,cy+34),(cx-28,cy+4)],outline=c,width=5)
def g_lines(d,cx,cy,c):
    for i,w in enumerate([52,38,46]):
        yy=cy-28+i*28; d.rounded_rectangle([cx-28,yy,cx-28+w,yy+9],4,fill=c)
def g_graph(d,cx,cy,c):
    top=(cx,cy-28);bl=(cx-28,cy+28);br=(cx+28,cy+28)
    d.line([top,bl],fill=c,width=5);d.line([top,br],fill=c,width=5)
    for p in (top,bl,br): d.ellipse([p[0]-12,p[1]-12,p[0]+12,p[1]+12],outline=c,width=5,fill=CARDBG)
def card(d,y0,title,glyph,fired,ev):
    H=270; acc=DK if fired else CLR
    d.rounded_rectangle([56,y0,PW-56,y0+H],26,fill=CARDBG,outline=CARDBD,width=2)
    bx,by=104,y0+38; d.rounded_rectangle([bx,by,bx+108,by+108],22,outline=acc,width=4); glyph(d,bx+54,by+54,acc)
    d.text((248,y0+50),title,font=bold(50),fill=INK if fired else (150,155,168))
    if fired:
        d.rounded_rectangle([PW-300,y0+46,PW-104,y0+114],32,fill=FIRED_BG); ctext(d,(2*PW-404)//2,y0+80,"FIRED",semi(31),(255,255,255))
    else:
        d.rounded_rectangle([PW-300,y0+46,PW-104,y0+114],32,outline=CLEAR_GRN,width=3); ctext(d,(2*PW-404)//2,y0+80,"CLEAR",semi(31),CLEAR_GRN)
    yy=y0+150
    for l in ev: d.text((104,yy),l,font=reg(44),fill=EVf if fired else EVc); yy+=58
    return H
def panel(cfg,Hpx):
    im=Image.new("RGBA",(PW,Hpx),(0,0,0,0)); d=ImageDraw.Draw(im,"RGBA")
    d.rounded_rectangle([2,2,PW-2,Hpx-2],40,fill=PANELBG,outline=PANELBD,width=3)
    d.rounded_rectangle([52,64,220,232],38,fill=(98,111,252)); d.line([(100,150),(126,180)],fill=(255,255,255),width=15); d.line([(126,180),(182,112)],fill=(255,255,255),width=15)
    d.text((272,78),"Donation Checker",font=bold(56),fill=INK)
    d.text((272,158),"crowdfunding risk check",font=reg(40),fill=SUB)
    d.line([(40,262),(PW-40,262)],fill=LINE,width=2)
    d.rounded_rectangle([56,316,PW-56,792],30,fill=cfg['abg'],outline=cfg['abd'],width=3)
    cx,cy=200,470;r=86; d.ellipse([cx-r,cy-r,cx+r,cy+r],fill=cfg['ic'])
    if cfg['icon']=='warn':
        d.polygon([(cx,cy-44),(cx-50,cy+38),(cx+50,cy+38)],outline=(255,255,255),width=7)
        d.line([(cx,cy-16),(cx,cy+12)],fill=(255,255,255),width=8); d.ellipse([cx-5,cy+22,cx+6,cy+33],fill=(255,255,255))
    elif cfg['icon']=='none':
        d.rounded_rectangle([cx-44,cy-10,cx+44,cy+10],10,fill=(255,255,255))
    else:
        d.line([(cx-40,cy+4),(cx-10,cy+34)],fill=(255,255,255),width=12); d.line([(cx-10,cy+34),(cx+44,cy-30)],fill=(255,255,255),width=12)
    d.text((330,396),cfg['title'],font=bold(64),fill=cfg['tc'])
    for i,l in enumerate(cfg['body']): d.text((330,496+i*70),l,font=reg(46),fill=EVf)
    by=856; x=64
    for ch in "SIGNAL BREAKDOWN": d.text((x,by),ch,font=semi(38),fill=LABELG); x+=tl(d,ch,semi(38))+7
    d.rounded_rectangle([PW-404,by-12,PW-56,by+62],36,fill=PILLBG,outline=(206,211,222),width=2)
    ctext(d,(2*PW-460)//2,by+25,cfg['pill'],semi(34),(70,77,95))
    y=964
    for (t,g,fired,ev) in cfg['cards']: y+=card(d,y,t,g,fired,ev)+44
    return im
A=("External signals (A)",g_shield); B=("Behavioral LLM (B)",g_lines); C=("Organizer identity (C)",g_graph)
def mk(fa,fb,fc,title,body,pill,icon,ic,abg,abd,tc):
    return dict(title=title,body=body,pill=pill,icon=icon,ic=ic,abg=abg,abd=abd,tc=tc,cards=[
        (A[0],A[1],fa,["Off-platform payment redirection","detected."] if fa else ["No off-platform routing detected."]),
        (B[0],B[1],fb,["Behavioral classifier: 4 of 10","manipulation flags."] if fb else ["Behavioral classifier below","threshold."]),
        (C[0],C[1],fc,["Payment account shared with 6 other","campaigns in one community."] if fc else ["No shared organizer identity found."]),
    ])
fraud=mk(1,1,1,"Fraud-shaped",["Two or more separate checks were","triggered. Probabilistic, not proof."],"3 signals fired","warn",(229,72,97),(253,235,239),(229,72,97),(178,32,58))
susp =mk(0,1,0,"Suspicious",["Exactly one check was triggered.","Automated and probabilistic."],"1 signal fired","warn",(240,158,42),(255,245,229),(240,158,42),(178,110,10))
clean=mk(0,0,0,"Unknown",["No checks were triggered. Absence of a","signal is not a guarantee."],"0 signals fired","none",(98,111,252),(238,241,255),(98,111,252),(64,74,190))
maxh=2010
ims=[panel(c,maxh) for c in (fraud,susp,clean)]
GAP=70;PAD=40; CW=3*PW+2*GAP+2*PAD; CH=maxh+2*PAD
canvas=Image.new("RGB",(CW,CH),(255,255,255))
for i,im in enumerate(ims):
    x=PAD+i*(PW+GAP)
    sh=Image.new("RGBA",(PW,maxh),(0,0,0,0)); ImageDraw.Draw(sh).rounded_rectangle([2,2,PW-2,maxh-2],40,fill=(0,0,0,40))
    canvas.paste((186,189,197),(x+6,PAD+10),sh.filter(ImageFilter.GaussianBlur(14)).split()[3])
    canvas.paste(im,(x,PAD),im)
canvas.save("images/extension_states.png"); print("saved",canvas.size)
