#!/usr/bin/env python3
"""Thread berantai ke Threads daripada siri.csv (skrip berasingan, tidak menyentuh posts.csv).
Lajur siri.csv: id,scheduled_at,parts,status,permalink,error
- scheduled_at = masa Malaysia "YYYY-MM-DD HH:MM"
- parts = bahagian thread dipisahkan dengan |||  (bahagian pertama = post utama, setiap bahagian seterusnya = reply kepada bahagian sebelumnya)
- status: pending -> posted / failed
Rahsia: THREADS_ACCESS_TOKEN, (pilihan) TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID.
"""
import csv, io, os, sys, time
from datetime import datetime, timedelta, timezone

import requests

API = "https://graph.threads.net/v1.0"
KL = timezone(timedelta(hours=8))
CSV_PATH = os.environ.get("SIRI_CSV", "siri.csv")
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
LEWAT_JAM = 6
COLS = ["id", "scheduled_at", "parts", "status", "permalink", "error"]


def alert(teks):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": teks[:3900], "disable_web_page_preview": "true"}, timeout=20)
    except Exception as e:
        print("alert Telegram gagal:", e)


def ringkas(r):
    c = (r.get("parts") or "").split("|||")[0].replace("\n", " ").strip()
    return (c[:80] + "...") if len(c) > 80 else c


def baca():
    if not os.path.exists(CSV_PATH):
        return list(COLS), []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        rows = list(rd)
        head = rd.fieldnames or list(COLS)
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
        time.sleep(3)
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
    bhg = [x.strip() for x in (row.get("parts") or "").split("|||") if x.strip()]
    if not bhg:
        raise RuntimeError("tiada kandungan dalam parts")
    prev, link = terbit(api("POST", "me/threads", media_type="TEXT", text=bhg[0][:500])["id"])
    ralat = ""
    siap = 1
    try:
        for t in bhg[1:]:
            time.sleep(4)
            prev, _ = terbit(api("POST", "me/threads", media_type="TEXT", text=t[:500], reply_to_id=prev)["id"])
            siap += 1
    except Exception as e:
        ralat = f"Bahagian {siap + 1} gagal: {str(e)[:200]}"
    return link, ralat, siap, len(bhg)


def jalan():
    if not TOKEN:
        sys.exit("THREADS_ACCESS_TOKEN tiada.")
    head, rows = baca()
    kini = datetime.now(KL)
    due = []
    for r in rows:
        if (r.get("status") or "pending").lower() not in ("pending", ""):
            continue
        try:
            at = datetime.strptime(r["scheduled_at"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=KL)
        except Exception:
            r["status"], r["error"] = "failed", "format scheduled_at salah"
            alert(f"Thread siri #{r.get('id')} GAGAL: format scheduled_at salah")
            continue
        if at <= kini:
            due.append((at, r))
    due.sort(key=lambda x: x[0])
    for at, r in due[:2]:
        if kini - at > timedelta(hours=LEWAT_JAM):
            r["status"], r["error"] = "failed", f"terlepas masa lebih {LEWAT_JAM} jam"
            alert(f"Thread siri #{r.get('id')} GAGAL: terlepas masa ({r.get('scheduled_at')})\n{ringkas(r)}")
            continue
        try:
            link, ralat, siap, jumlah = hantar(r)
            r["status"], r["permalink"], r["error"] = "posted", link, ralat
            print("POSTED", r.get("id"), link, ralat)
            alert(f"Thread siri #{r.get('id')} BERJAYA ({r.get('scheduled_at')}) {siap}/{jumlah} bahagian\n{ringkas(r)}\n{link or ''}" + (f"\nAmaran: {ralat}" if ralat else ""))
        except Exception as e:
            r["status"], r["error"] = "failed", str(e)[:300]
            print("FAILED", r.get("id"), e)
            alert(f"Thread siri #{r.get('id')} GAGAL ({r.get('scheduled_at')})\nSebab: {str(e)[:300]}\n{ringkas(r)}")
        simpan(head, rows)
    simpan(head, rows)


if __name__ == "__main__":
    jalan()
