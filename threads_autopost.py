#!/usr/bin/env python3
"""Auto-post ke Threads daripada posts.csv (dijalankan oleh GitHub Actions setiap ~10 minit).

Lajur posts.csv: id,platform,scheduled_at,type,media_urls,caption,status,permalink,error,
topic_tag,reply_text,reply_media,ref
 - scheduled_at = masa Malaysia "YYYY-MM-DD HH:MM"
 - status: pending -> posted / failed (skip = dibatalkan)
 - reply_text = reply pertama (link affiliate). reply_media='image' = lampirkan gambar sama dalam reply.
Rahsia (GitHub Secrets): THREADS_ACCESS_TOKEN, (pilihan) GH_PAT untuk perbaharui token automatik.
"""
import base64, csv, io, os, sys, time
from datetime import datetime, timedelta, timezone

import requests

API = "https://graph.threads.net/v1.0"
KL = timezone(timedelta(hours=8))
CSV_PATH = os.environ.get("POSTS_CSV", "posts.csv")
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
LEWAT_JAM = 12   # post yang terlepas lebih daripada ini ditanda failed (elak post lama keluar tiba-tiba)
COLS = ["id", "platform", "scheduled_at", "type", "media_urls", "caption", "status", "permalink", "error",
        "topic_tag", "reply_text", "reply_media", "ref"]


def baca():
    if not os.path.exists(CSV_PATH):
        return list(COLS), []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        rows = list(r)
        head = r.fieldnames or list(COLS)
    for c in COLS:
        if c not in head:
            head.append(c)
    return head, rows


def simpan(head, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=head, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({h: r.get(h, "") or "" for h in head})
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def api(method, path, **params):
    params["access_token"] = TOKEN
    r = requests.request(method, f"{API}/{path}", params=params, timeout=60)
    try:
        j = r.json()
    except Exception:
        j = {}
    if not r.ok or "error" in j:
        e = j.get("error", {})
        raise RuntimeError(f"Threads {r.status_code}: {e.get('message') or r.text[:200]}")
    return j


def tunggu(cid, maks=180):
    t0 = time.time()
    while time.time() - t0 < maks:
        s = api("GET", cid, fields="status,error_message")
        if s.get("status") == "FINISHED":
            return
        if s.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError("Container " + s.get("status") + ": " + str(s.get("error_message", "")))
        time.sleep(4)
    raise RuntimeError("Container tak siap dalam masa")


def terbit(cid):
    tunggu(cid)
    pid = api("POST", "me/threads_publish", creation_id=cid)["id"]
    link = ""
    for _ in range(3):
        try:
            link = api("GET", pid, fields="permalink").get("permalink", "")
            break
        except Exception:
            time.sleep(3)
    return pid, link


def hantar(row):
    jenis = (row.get("type") or "TEXT").upper()
    media = (row.get("media_urls") or "").split("|")[0].strip()
    teks = (row.get("caption") or "")[:500]
    p = {"media_type": jenis if jenis in ("IMAGE", "VIDEO") and media else "TEXT", "text": teks}
    if p["media_type"] == "IMAGE":
        p["image_url"] = media
    elif p["media_type"] == "VIDEO":
        p["video_url"] = media
    tag = (row.get("topic_tag") or "").strip()
    if tag:
        p["topic_tag"] = tag
    pid, link = terbit(api("POST", "me/threads", **p)["id"])
    ralat_reply = ""
    reply = (row.get("reply_text") or "").strip()
    if reply:
        try:
            time.sleep(5)
            q = {"media_type": "TEXT", "text": reply[:500], "reply_to_id": pid}
            if (row.get("reply_media") or "") == "image" and p["media_type"] == "IMAGE":
                q.update(media_type="IMAGE", image_url=media)
            terbit(api("POST", "me/threads", **q)["id"])
        except Exception as e:  # post utama dah keluar; jangan hantar semula
            ralat_reply = "Post keluar tetapi reply gagal: " + str(e)
    return link, ralat_reply


def jalan():
    if not TOKEN:
        sys.exit("THREADS_ACCESS_TOKEN tiada (GitHub → Settings → Secrets → Actions).")
    head, rows = baca()
    kini = datetime.now(KL)
    ubah = False
    due = []
    for r in rows:
        if (r.get("status") or "pending").lower() not in ("pending", ""):
            continue
        try:
            at = datetime.strptime(r["scheduled_at"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=KL)
        except Exception:
            r["status"], r["error"] = "failed", "format scheduled_at salah"; ubah = True
            continue
        if at <= kini:
            due.append((at, r))
    due.sort(key=lambda x: x[0])
    for at, r in due[:3]:  # maksimum 3 sekali jalan
        if kini - at > timedelta(hours=LEWAT_JAM):
            r["status"], r["error"] = "failed", f"terlepas masa lebih {LEWAT_JAM} jam"; ubah = True
            continue
        try:
            link, ralat = hantar(r)
            r["status"], r["permalink"], r["error"] = "posted", link, ralat
            print("POSTED", r.get("id"), link, ralat)
        except Exception as e:
            r["status"], r["error"] = "failed", str(e)[:300]
            print("FAILED", r.get("id"), e)
        ubah = True
        simpan(head, rows)  # simpan segera supaya tak dipost dua kali kalau langkah seterusnya mati
    if ubah:
        simpan(head, rows)


def refresh():
    """Perbaharui token Threads (sah 60 hari) dan simpan semula sebagai GitHub Secret (perlu GH_PAT + PyNaCl)."""
    pat, repo = os.environ.get("GH_PAT", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not (TOKEN and pat and repo):
        print("refresh dilangkau (perlu THREADS_ACCESS_TOKEN, GH_PAT)."); return
    r = requests.get("https://graph.threads.net/refresh_access_token",
                     params={"grant_type": "th_refresh_access_token", "access_token": TOKEN}, timeout=60)
    j = r.json()
    if "access_token" not in j:
        print("refresh gagal:", j); return
    from nacl import encoding, public
    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    k = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=h, timeout=60).json()
    box = public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder()))
    enc = base64.b64encode(box.encrypt(j["access_token"].encode())).decode()
    u = requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/THREADS_ACCESS_TOKEN", headers=h,
                     json={"encrypted_value": enc, "key_id": k["key_id"]}, timeout=60)
    print("token dibaharui:", u.status_code)


if __name__ == "__main__":
    {"refresh": refresh}.get(sys.argv[1] if len(sys.argv) > 1 else "", jalan)()
