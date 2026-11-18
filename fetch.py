import json, os, re, time, difflib, datetime as dt, urllib.parse
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
    # Bekannte Wargaming-Events und -Turnierserien
    ("Google News", gn('Adepticon OR "NOVA Open" OR "Las Vegas Open" OR "Frontline Gaming" OR "Independent Tournament Circuit" OR "Warhammer Fest" OR "Bolt Action" OR "Trench Crusade" OR "Flames of War" event OR tournament OR championship'), "tabletop"),
    # Kunden-Chancen: Turnierserien, Übertragungen, Partnerschaften
    ("Google News", gn('"championship series" OR "world championship" OR "organized play" "card game" OR wargame OR miniatures stream OR broadcast OR livestream'), "tcg"),
    ("Google News", gn('tabletop OR wargame OR "trading card game" tournament sponsor OR partnership OR "media partner" OR esports'), "tabletop"),
    ("Google News", gn('Regional Championship OR "Grand Prix" OR "Open" new tournament circuit announced card game OR miniatures'), "tcg"),
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

PROMPT = """Heute ist {heute}. Du filterst Meldungen für einen Business-Development-Manager. Seine Firma verkauft Übertragungen (Broadcasts) und Wettbewerbs-Events an Hersteller und Veranstalter von Miniatur-Wargames und Sammelkartenspielen (TCG).

Wichtigste Kategorie ist "Chance": mögliche Kunden, die Turniere veranstalten oder planen und Übertragungen brauchen könnten. Dazu zählen: Hersteller und Veranstalter von Wargame- oder TCG-Turnieren, Turnierserien, Weltmeisterschaften, Regionals, Meisterschaften, Ligen, Organized Play, Messen mit Turnieren, neue Turnierformate sowie Medien-, Streaming- und Sponsoring-Partnerschaften. Behalte SOLCHE Meldungen immer, auch wenn sie klein sind. Nenne in der Zusammenfassung, soweit im Text: wer veranstaltet, was, wann, wo, und ob eine Übertragung erwähnt wird (sonst schreibe "Übertragung nicht erwähnt").

Behalte außerdem:
- Neue oder kleine TCGs und Miniaturenspiele, die wachsen könnten, Ankündigungen und Trends (Kategorien TCG-Ankündigung, TCG-Trend).
- Geschäftszahlen, Übernahmen und Vertriebsdeals von Wargame-/TCG-Firmen (Kategorie Hersteller).
- IMMER die Wochenübersicht "Games Industry News Roundup" (Kategorie Hersteller).
- Games Workshop (Warhammer) nur, wenn ein konkretes Event oder Turnier mit Ort, Datum und Veranstalter angekündigt wird.

Verwirf alles andere, besonders: Wer ein Turnier gewonnen hat, Ergebnisse, Siegerlisten, Interviews mit Spielern, Meta-Statistiken und Deck-Anteile, Taktik, Rezensionen, Angebote, Listicles, Wochenend-Zusammenfassungen von Turnieren. Verwirf Brettspiele, Rollenspiele, Videospiele, Crowdfunding von Brettspielen und Kunst-Meldungen. Ausnahme: Es geht um ein Wargame oder TCG oder um ein Turnier dafür.

Bereits stattgefundene Turniere verwerfen, außer die Spielerzahl wird genannt und liegt bei 300 oder mehr (zeigt, wie groß der Veranstalter ist). Verwirf auch reine Ergebnislisten.
Fasse nur zusammen, was in Titel und Text steht. Erfinde nichts.
Antworte ausschließlich mit einem JSON-Array. Pro behaltene Meldung: {"i": Nummer, "bereich": "tabletop" oder "tcg", "kategorie": "Chance" | "Event" | "Hersteller" | "TCG-Ankündigung" | "TCG-Trend", "spiel": "Name oder leer", "zusammenfassung": "1-2 Sätze auf Deutsch"}.

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
                                       messages=[{"role": "user", "content": PROMPT.replace("{heute}", dt.date.today().isoformat()) + liste}])
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

SEITEN_PROMPT = """Heute ist {heute}. Ist die Seite eine Fehlerseite, leer oder zeigt nur Menüpunkte, antworte mit []. Verwirf Ergebnislisten, Sieger, Meta-Statistiken (Deck-Anteile), Brettspiele und bereits stattgefundene Turniere (außer mit 300 oder mehr Spielern).
Aus dem Seitentext unten: hole alles heraus, was für einen Business-Development-Manager nützlich ist, der Übertragungen und Wettbewerbs-Events an Tabletop- und TCG-Hersteller verkauft.
Kategorie Chance: Turniere, Turnierserien und Events von Wargame- oder TCG-Veranstaltern, die Übertragungen brauchen könnten; nenne Veranstalter, Ort, Datum und ob eine Übertragung erwähnt wird. Keine Ergebnisse, Sieger, Meta oder Brettspiele.\nTabletop-Seiten: kommende Turniere und Events (Name, Ort, Datum, Spiel, Spielerzahl, Veranstalter, soweit genannt), sonst neue relevante Meldungen. Games Workshop nur bei Events.
TCG-Seiten: Ankündigungen, neue oder kleine TCGs, Trends, größere Turniere.
Nur Dinge, die im Text stehen, nichts erfinden. Die URL muss exakt aus den Links im Text stammen (steht in eckigen Klammern hinter dem Linktext).
Antworte nur mit einem JSON-Array aus Objekten: {"title","url","date":"JJJJ-MM-TT oder leer","bereich":"tabletop" oder "tcg","kategorie":"Chance" | "Event" | "Hersteller" | "TCG-Ankündigung" | "TCG-Trend","spiel","zusammenfassung":"1-2 Sätze auf Deutsch"}.

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
                                       messages=[{"role": "user", "content": SEITEN_PROMPT.replace("{heute}", dt.date.today().isoformat()) + f"Seite: {name} ({bereich})\n" + text}])
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

BRIEF_PROMPT = """Schreibe den Morgenbrief für Gerrit. Er arbeitet im Business Development bei einem Unternehmen, das Übertragungen (Broadcasts) und Wettbewerbs-Events an Hersteller und Veranstalter von Miniatur-Wargames und Sammelkartenspielen verkauft. Er liest den Brief morgens beim Kaffee oder lässt ihn sich vorlesen.

Wichtig für Gerrit: mögliche Kunden, die Turniere veranstalten und Übertragungen brauchen könnten. Darüber will er alles wissen, immer. Nicht interessant für ihn: wer ein Turnier gewonnen hat, Ergebnisse, Meta-Statistiken, Brettspiele.

Ton: freundlich, klar und ruhig, wie ein netter Kollege, der beim Kaffee kurz berichtet. Du-Form. Kurze Sätze, einfache Wörter. Wenige englische Begriffe.
Form für Vorlesen: reiner Fließtext in kurzen Absätzen, durch Leerzeilen getrennt. Keine Aufzählungszeichen, keine Überschriften mit Sonderzeichen, keine Links, keine Klammern, keine Emojis.
Aufbau:
1. Begrüßung mit dem heutigen Datum: {datum}.
2. Kunden und Chancen (der größte Teil): Gehe alle Meldungen der Kategorie Chance durch, nichts auslassen. Nenne pro Eintrag: wer veranstaltet, was, wann, wo, und ob eine Übertragung erwähnt wird. Steht dazu nichts in der Meldung, sag, dass es offen ist und eine Nachfrage lohnt. Fasse ähnliche Einträge zusammen, aber nenne die Namen. Sortiere: zuerst Wargames, dann TCG, zuerst das, was bald stattfindet.
3. Kurz Tabletop: was bei Herstellern passiert (Geschäftszahlen, neue Spiele, Veranstaltungen).
4. Kurz TCG: neue oder kleinere Spiele, die auffallen, und worüber gesprochen wird.
5. Ein freundlicher Abschluss.
Regeln: Nutze ausschließlich Fakten aus den Meldungen unten, erfinde nichts, und nenne die Quelle im Satz (zum Beispiel: laut Limitless). Gib nur Daten und Zahlen an, die in den Meldungen stehen. Ist wenig los, sag das ehrlich. Länge: etwa 400 bis 550 Wörter. Mehr nur, wenn sehr viele Chancen vorliegen.

Meldungen der letzten Tage:
"""

def morgenbrief(items):
    heute = dt.date.today()
    grenze = (heute - dt.timedelta(days=7)).isoformat()
    alle_frisch = [x for x in items if x["date"] >= grenze]
    frisch = [x for x in alle_frisch if x["kategorie"] == "Chance"][:30] + [x for x in alle_frisch if x["kategorie"] != "Chance"][:15]
    if not frisch:
        return None
    liste = "\n".join(f"- [{x['bereich']}, {x['kategorie']}] {x['title']} ({x['source']}, {x['date']}): {x['zusammenfassung']}" for x in frisch)
    datum = f"{TAGE_DE[heute.weekday()]}, {heute.day}. {MONATE[heute.month - 1]} {heute.year}"
    r = Anthropic().messages.create(model="claude-sonnet-5-5", max_tokens=6000,
                                    messages=[{"role": "user", "content": BRIEF_PROMPT.replace("{datum}", datum) + liste}])
    return "".join(b.text for b in r.content if b.type == "text").strip()

def entdoppeln(items):
    """Gleiche Meldungen aus mehreren Quellen zusammenführen; Chancen haben Vorrang."""
    out, gesehen = [], []
    for x in sorted(items, key=lambda x: x["kategorie"] != "Chance"):
        t = re.sub(r"\W+", " ", x["title"].lower()).strip()
        doppelt = False
        for d, q, u in gesehen:
            if d != x["date"]:
                continue
            r = difflib.SequenceMatcher(None, t, u).ratio()
            if r >= (0.95 if q == x["source"] else 0.55):
                doppelt = True
                break
        if not doppelt:
            gesehen.append((x["date"], x["source"], t))
            out.append(x)
    return out

def main():
    try:
        alt_daten = json.load(open("data.json", encoding="utf-8"))
    except Exception:
        alt_daten = {}
    alt = alt_daten.get("items", [])
    neu = filtern(sammeln()) + seiten_lesen()
    grenze = (dt.date.today() - dt.timedelta(days=BEHALTEN)).isoformat()
    alle = {x["url"]: x for x in alt + neu if x["date"] >= grenze}
    items = sorted(entdoppeln(list(alle.values())), key=lambda x: x["date"], reverse=True)
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
