import json, os, re, time, datetime as dt, urllib.parse
import feedparser
from anthropic import Anthropic

TAGE = 7  # so weit zurück werden neue Meldungen gesucht
BEHALTEN = 30  # so lange bleiben Einträge auf der Seite

# Direkte Feeds (Adressen beim ersten Lauf prüfen, kaputte einfach löschen)
FEEDS = [
    ("Goonhammer", "https://www.goonhammer.com/feed/", "tabletop"),
    ("Tabletop Gaming", "https://www.tabletopgaming.co.uk/feed/", "tabletop"),
]

# Google-News-Suchen: ein Suchbegriff pro Zeile, beliebig erweiterbar
def gn(q):
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(q + f" when:{TAGE}d") + "&hl=en&gl=US&ceid=US:en"

SUCHEN = [
    ("Google News", gn('"Bolt Action" OR "Konflikt 47" OR "Trench Crusade" tournament'), "tabletop"),
    ("Google News", gn('wargame OR tabletop tournament announced -"Games Workshop"'), "tabletop"),
    ("Google News", gn('"new TCG" announced OR launch OR kickstarter'), "tcg"),
    ("Google News", gn('upcoming trading card game indie OR "new card game" tournament'), "tcg"),
]

def sammeln():
    grenze = time.time() - TAGE * 86400
    gesehen, items = set(), []
    for name, url, bereich in FEEDS + SUCHEN:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print("Fehler", url, e)
            continue
        for e in feed.entries:
            link = e.get("link")
            ts = time.mktime(e.published_parsed) if e.get("published_parsed") else time.time()
            if not link or link in gesehen or ts < grenze:
                continue
            gesehen.add(link)
            text = re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:400]
            quelle = e.get("source", {}).get("title", name) if name == "Google News" else name
            items.append({"url": link, "title": e.get("title", ""), "source": quelle,
                          "date": dt.date.fromtimestamp(ts).isoformat(), "text": text, "bereich": bereich})
    print(len(items), "Meldungen gesammelt")
    return items[:90]

PROMPT = """Du filterst Meldungen für einen Business-Development-Manager, der Übertragungen und Wettbewerbs-Events an Hersteller von Tabletop- und Sammelkartenspielen verkauft.
Behalte NUR Meldungen, die dafür wirklich nützlich sind:
- Tabletop: Turniere und Events (wo, wer veranstaltet), und was Hersteller außer Games Workshop gerade tun. Games Workshop nur bei Events.
- TCG: Ankündigungen, neue oder kleine TCGs, die wachsen könnten, Trends, worüber Spieler sprechen.
Verwirf alles andere (Rezensionen, Angebote, Listicles, reine Spiel-Taktik).
Fasse nur zusammen, was in Titel und Text steht. Erfinde nichts.
Antworte ausschließlich mit einem JSON-Array. Pro behaltene Meldung: {"i": Nummer, "bereich": "tabletop" oder "tcg", "kategorie": "Event" | "Hersteller" | "TCG-Ankündigung" | "TCG-Trend", "spiel": "Name oder leer", "zusammenfassung": "1-2 Sätze auf Deutsch"}.

Meldungen:
"""

def filtern(items):
    if not items:
        return []
    client = Anthropic()
    liste = "\n".join(f"{i}. {x['title']} | {x['source']} | {x['text']}" for i, x in enumerate(items))
    r = client.messages.create(model="claude-haiku-5-5", max_tokens=4000,
                               messages=[{"role": "user", "content": PROMPT + liste}])
    raw = re.sub(r"^```(json)?|```$", "", r.content[0].text.strip(), flags=re.M).strip()
    out = []
    for k in json.loads(raw):
        x = items[k["i"]]
        out.append({"url": x["url"], "title": x["title"], "source": x["source"], "date": x["date"],
                    "bereich": k["bereich"], "kategorie": k["kategorie"], "spiel": k.get("spiel", ""),
                    "zusammenfassung": k["zusammenfassung"]})
    return out

# Seiten ohne Feed: Claude liest den Seitentext und holt Events und Meldungen heraus.
# Funktioniert nur bei Seiten, die ihren Inhalt direkt ausliefern (nicht bei reinen JavaScript-Seiten).
SEITEN = [
    ("T³ Turniere (DACH)", "https://www.tabletopturniere.de/", "tabletop"),
    ("Frontline Gaming ITC", "https://www.frontlinegaming.org/itc-battles/", "tabletop"),
    ("Tabletop Battles", "https://www.tabletopbattles.com/", "tabletop"),
    ("Limitless TCG", "https://limitlesstcg.com/", "tcg"),
    ("Limitless Play", "https://play.limitlesstcg.com/", "tcg"),
]

SEITEN_PROMPT = """Aus dem Seitentext unten: hole alles heraus, was für einen Business-Development-Manager nützlich ist, der Übertragungen und Wettbewerbs-Events an Tabletop- und TCG-Hersteller verkauft.
Tabletop-Seiten: kommende Turniere und Events (Name, Ort, Datum, Spiel, Spielerzahl, Veranstalter, soweit genannt), sonst neue relevante Meldungen. Games Workshop nur bei Events.
TCG-Seiten: Ankündigungen, neue oder kleine TCGs, Trends, größere Turniere.
Nur Dinge, die im Text stehen, nichts erfinden. Die URL muss exakt aus den Links im Text stammen (steht in eckigen Klammern hinter dem Linktext).
Antworte nur mit einem JSON-Array aus Objekten: {"title","url","date":"JJJJ-MM-TT oder leer","bereich":"tabletop" oder "tcg","kategorie":"Event" | "Hersteller" | "TCG-Ankündigung" | "TCG-Trend","spiel","zusammenfassung":"1-2 Sätze auf Deutsch"}.

Seitentext:
"""

def seiten_lesen():
    import requests
    from urllib.parse import urljoin
    client = Anthropic()
    out = []
    for name, url, bereich in SEITEN:
        try:
            h = requests.get(url, headers={"User-Agent": "Mozilla/5.0 markt-radar"}, timeout=25).text
        except Exception as e:
            print("Fehler", url, e)
            continue
        links = set()
        def ersetze(m):
            u = urljoin(url, m.group(1))
            links.add(u)
            titel = re.sub(r"<[^>]+>", "", m.group(2))
            return f" {titel} [{u}] "
        h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", h)
        h = re.sub(r'(?is)<a [^>]*href="([^"#][^"]*)"[^>]*>(.*?)</a>', ersetze, h)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))[:14000]
        if len(text) < 500:
            print(name, ": kaum Text, vermutlich JavaScript-Seite")
            continue
        try:
            r = client.messages.create(model="claude-haiku-5-5", max_tokens=3000,
                                       messages=[{"role": "user", "content": SEITEN_PROMPT + f"Seite: {name} ({bereich})\n" + text}])
            raw = re.sub(r"^```(json)?|```$", "", r.content[0].text.strip(), flags=re.M).strip()
            for k in json.loads(raw):
                if k.get("url") in links:
                    out.append({"url": k["url"], "title": k["title"], "source": name,
                                "date": k.get("date") or dt.date.today().isoformat(),
                                "bereich": k.get("bereich", bereich), "kategorie": k.get("kategorie", "Event"),
                                "spiel": k.get("spiel", ""), "zusammenfassung": k["zusammenfassung"]})
        except Exception as e:
            print("Fehler beim Lesen von", name, e)
    print(len(out), "Einträge aus Seiten")
    return out

def main():
    try:
        alt = json.load(open("data.json", encoding="utf-8"))["items"]
    except Exception:
        alt = []
    neu = filtern(sammeln()) + seiten_lesen()
    grenze = (dt.date.today() - dt.timedelta(days=BEHALTEN)).isoformat()
    alle = {x["url"]: x for x in alt + neu if x["date"] >= grenze}
    items = sorted(alle.values(), key=lambda x: x["date"], reverse=True)
    json.dump({"stand": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "items": items},
              open("data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(neu), "neu,", len(items), "gesamt")

main()
      
