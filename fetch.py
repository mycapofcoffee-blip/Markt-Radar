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
def gn(q, de=False):
    land = "&hl=de&gl=DE&ceid=DE:de" if de else "&hl=en&gl=US&ceid=US:en"
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(q + f" when:{TAGE}d") + land

SUCHEN = [
    ("Google News", gn('"Bolt Action" OR "Konflikt 47" OR "Trench Crusade" tournament'), "tabletop"),
    ("Google News", gn('wargame OR tabletop tournament announced -"Games Workshop"'), "tabletop"),
    ("Google News", gn('"new TCG" announced OR launch OR kickstarter'), "tcg"),
    ("Google News", gn('upcoming trading card game indie OR "new card game" tournament'), "tcg"),
    # Branchenseiten
    ("Google News", gn('site:icv2.com tabletop OR "card game" OR miniatures'), "tabletop"),
    ("Google News", gn('site:wargamer.com OR site:beastsofwar.com OR site:belloflostsouls.net tournament OR event'), "tabletop"),
    ("Google News", gn('site:gamingtrend.com boardgamewire OR tabletop'), "tabletop"),
    # Tabletop-Spiele und Veranstaltungen
    ("Google News", gn('"Old World" OR "Marvel Crisis Protocol" OR "Star Wars Legion" OR "Battletech" OR "Infinity" OR "Warcrow" OR "Kill Team" tournament OR championship OR open'), "tabletop"),
    ("Google News", gn('Gamefound OR Kickstarter wargame OR miniatures game launch'), "tabletop"),
    ("Google News", gn('Tabletop Turnier OR Wargaming Event OR "Warhammer" Turnier Deutschland', de=True), "tabletop"),
    # TCG: etablierte und aufstrebende Spiele
    ("Google News", gn('Riftbound OR "Gundam Card Game" OR "Star Wars: Unlimited" OR "Flesh and Blood" OR Lorcana OR "One Piece Card Game" tournament OR announced OR championship'), "tcg"),
    ("Google News", gn('"Union Arena" OR "Grand Archive" OR "Sorcery: Contested Realm" OR "Altered TCG" OR "Digimon Card Game" OR "Weiss Schwarz" OR "Shadowverse" announced OR tournament'), "tcg"),
    ("Google News", gn('TCG tournament series OR "world championship" OR "organized play" announced'), "tcg"),
    ("Google News", gn('Sammelkartenspiel neu OR Turnier OR Ankündigung TCG', de=True), "tcg"),
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
    return items[:240]

PROMPT = """Du filterst Meldungen für einen Business-Development-Manager, der Übertragungen und Wettbewerbs-Events an Hersteller von Tabletop- und Sammelkartenspielen verkauft.
Behalte NUR Meldungen, die dafür wirklich nützlich sind:
- Tabletop: Turniere und Events (wo, wer veranstaltet), und was Hersteller außer Games Workshop gerade tun. Games Workshop nur bei Events.
- TCG: Ankündigungen, neue oder kleine TCGs, die wachsen könnten, Trends, worüber Spieler sprechen.
Verwirf alles andere, besonders: Rezensionen, Angebote, Listicles, Taktik- und Meta-Berichte, Interviews mit Turniergewinnern, Turnier-Wochenend-Zusammenfassungen (Weekend Update), Rollenspiel-, Videospiel- und Kunst-Meldungen ohne Tabletop- oder TCG-Turnier.
Games Workshop (Warhammer): nur behalten, wenn ein konkretes Event oder Turnier angekündigt wird (Ort, Datum, Veranstalter). Nicht behalten: Meta, Listen, Interviews, Ergebnisberichte.
Behalte IMMER die Wochenübersicht "Games Industry News Roundup" (Kategorie Hersteller).
Behalte zusätzlich Geschäftszahlen, Übernahmen und Vertriebsdeals von Tabletop-/TCG-Firmen (Kategorie Hersteller).
Fasse nur zusammen, was in Titel und Text steht. Erfinde nichts.
Antworte ausschließlich mit einem JSON-Array. Pro behaltene Meldung: {"i": Nummer, "bereich": "tabletop" oder "tcg", "kategorie": "Event" | "Hersteller" | "TCG-Ankündigung" | "TCG-Trend", "spiel": "Name oder leer", "zusammenfassung": "1-2 Sätze auf Deutsch"}.

Meldungen:
"""

def filtern(items):
    if not items:
        return []
    client = Anthropic()
    out = []
    for start in range(0, len(items), 40):  # in Päckchen, damit die Antwort nie abgeschnitten wird
        teil = items[start:start + 40]
        liste = "\n".join(f"{i}. {x['title']} | {x['source']} | {x['text']}" for i, x in enumerate(teil))
        try:
            r = client.messages.create(model="claude-haiku-5-5", max_tokens=8000,
                                       messages=[{"role": "user", "content": PROMPT + liste}])
            if r.stop_reason == "max_tokens":
                print("Warnung: Antwort in Päckchen", start, "abgeschnitten")
            text = "".join(b.text for b in r.content if b.type == "text").strip()
            raw = re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip()
            for k in json.loads(raw):
                x = teil[k["i"]]
                out.append({"url": x["url"], "title": x["title"], "source": x["source"], "date": x["date"],
                            "bereich": k["bereich"], "kategorie": k["kategorie"], "spiel": k.get("spiel", ""),
                            "zusammenfassung": k["zusammenfassung"]})
        except Exception as e:
            print("Fehler beim Filtern, Päckchen", start, e)
    print(len(out), "Meldungen behalten")
    return out

# Seiten ohne Feed: Claude liest den Seitentext und holt Events und Meldungen heraus.
# Funktioniert nur bei Seiten, die ihren Inhalt direkt ausliefern (nicht bei reinen JavaScript-Seiten).
SEITEN = [
    ("T³ Turniere (DACH)", "https://www.tabletopturniere.de/", "tabletop"),
    ("Frontline Gaming ITC", "https://www.frontlinegaming.org/itc-battles/", "tabletop"),
    ("Tabletop Battles", "https://www.tabletopbattles.com/", "tabletop"),
    ("Goonhammer Industry News", "https://www.goonhammer.com/tag/industry-news/", "tabletop"),
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
            r = client.messages.create(model="claude-haiku-5-5", max_tokens=6000,
                                       messages=[{"role": "user", "content": SEITEN_PROMPT + f"Seite: {name} ({bereich})\n" + text}])
            raw = re.sub(r"^```(json)?|```$", "", "".join(b.text for b in r.content if b.type == "text").strip(), flags=re.M).strip()
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

TAGE_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]

BRIEF_PROMPT = """Schreibe den Morgenbrief für Gerrit. Er arbeitet im Business Development bei einem Unternehmen, das Übertragungen und Wettbewerbs-Events an Tabletop- und TCG-Hersteller verkauft, und liest den Brief morgens beim Kaffee oder lässt ihn sich vorlesen.

Ton: freundlich, klar und ruhig, wie ein netter Kollege, der beim Kaffee kurz berichtet. Du-Form. Kurze Sätze, einfache Wörter, keine Fachjargon-Ketten. Wenige englische Begriffe.
Form für Vorlesen: reiner Fließtext in kurzen Absätzen, durch Leerzeilen getrennt. Keine Aufzählungszeichen, keine Überschriften mit Sonderzeichen, keine Links, keine Klammern, keine Emojis.
Aufbau:
1. Begrüßung mit dem heutigen Datum: {datum}.
2. Das Wichtigste in ein bis zwei Sätzen.
3. Tabletop: was passiert, welche Turniere und Events sind angekündigt.
4. TCG: was ist neu, und besonders: welche kleineren oder neuen Spiele fallen auf und worüber wird gesprochen.
5. Mögliche Ansatzpunkte für das Geschäft: nur wenn sie sich klar aus den Meldungen ergeben, zum Beispiel ein neues Turnier ohne Übertragung. Sonst weglassen.
6. Ein kurzer freundlicher Abschluss.
Regeln: Nutze ausschließlich Fakten aus den Meldungen unten, erfinde nichts und nenne die Quelle im Satz (zum Beispiel: laut Goonhammer). Gib nur Daten und Zahlen an, die in den Meldungen stehen. Ist wenig los, sag das ehrlich und kurz. Länge: etwa 300 bis 400 Wörter, nicht mehr.

Meldungen der letzten Tage:
"""

def morgenbrief(items):
    heute = dt.date.today()
    grenze = (heute - dt.timedelta(days=7)).isoformat()
    frisch = [x for x in items if x["date"] >= grenze][:40]
    if not frisch:
        return None
    liste = "\n".join(f"- [{x['bereich']}, {x['kategorie']}] {x['title']} ({x['source']}, {x['date']}): {x['zusammenfassung']}" for x in frisch)
    datum = f"{TAGE_DE[heute.weekday()]}, {heute.day}. {MONATE[heute.month - 1]} {heute.year}"
    r = Anthropic().messages.create(model="claude-sonnet-5-5", max_tokens=6000,
                                    messages=[{"role": "user", "content": BRIEF_PROMPT.replace("{datum}", datum) + liste}])
    return "".join(b.text for b in r.content if b.type == "text").strip()

def main():
    try:
        alt_daten = json.load(open("data.json", encoding="utf-8"))
    except Exception:
        alt_daten = {}
    alt = alt_daten.get("items", [])
    neu = filtern(sammeln()) + seiten_lesen()
    grenze = (dt.date.today() - dt.timedelta(days=BEHALTEN)).isoformat()
    alle = {x["url"]: x for x in alt + neu if x["date"] >= grenze}
    items = sorted(alle.values(), key=lambda x: x["date"], reverse=True)
    brief = alt_daten.get("brief")
    try:
        text = morgenbrief(items)
        if text:
            brief = {"text": text, "datum": dt.date.today().isoformat()}
    except Exception as e:
        print("Fehler beim Morgenbrief", e)
    json.dump({"stand": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "brief": brief, "items": items},
              open("data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(neu), "neu,", len(items), "gesamt")

main()
