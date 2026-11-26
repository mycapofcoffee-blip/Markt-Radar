import json, os, re, time, html, difflib, datetime as dt, urllib.parse
from collections import Counter
import feedparser
import requests
from anthropic import Anthropic

TAGE = 7  # so weit zurück werden neue Meldungen gesucht
BEHALTEN = 30  # so lange bleiben Einträge auf der Seite

# Direkte Feeds (Adressen beim ersten Lauf prüfen, kaputte einfach löschen)
FEEDS = [
    ("Goonhammer", "https://www.goonhammer.com/feed/", "tabletop"),
    ("Tabletop Gaming", "https://www.tabletopgaming.co.uk/feed/", "tabletop"),
    ("ICv2", "https://icv2.com/rss", "tabletop"),
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
    ("Google News", gn('site:wargamer.com OR site:beastsofwar.com OR site:belloflostsouls.net wargame OR miniatures OR skirmish OR "pre-order" OR tournament OR event'), "tabletop"),
    ("Google News", gn('"miniature wargame" OR "skirmish wargame" OR "miniatures game" announced OR "pre-order" OR Kickstarter OR Gamefound'), "tabletop"),
    ("Google News", gn('new wargame OR "new miniatures game" OR "new skirmish game" licensed OR "Monster Fight Club" OR "Atomic Mass" OR "Corvus Belli" OR "Mantic"'), "tabletop"),
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

# Zusätzliche, neutrale Suchen: Grundrauschen für die Buzz-Auswertung
SUCHEN += [
    ("Google News", gn('TCG OR "trading card game" OR "collectible card game"'), "tcg"),
    ("Google News", gn('wargame OR "miniatures game" OR "tabletop miniatures"'), "tabletop"),
]

MODELL_KLEIN = "claude-haiku-5-5"
MODELL_GROSS = "claude-sonnet-5-5"
MAX_ROH = 350

def claude_json(client, prompt, max_tokens=8000):
    r = client.messages.create(model=MODELL_KLEIN, max_tokens=max_tokens,
                               messages=[{"role": "user", "content": prompt}])
    if r.stop_reason == "max_tokens":
        print("Warnung: Antwort abgeschnitten")
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    return json.loads(re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip())

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
            text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary", ""))))[:400]
            quelle = e.get("source", {}).get("title", name) if name == "Google News" else name
            items.append({"url": link, "title": e.get("title", ""), "source": quelle,
                          "date": dt.date.fromtimestamp(ts).isoformat(), "text": text, "bereich": bereich})
    print(len(items), "Meldungen gesammelt")
    return items[:MAX_ROH]

# ---------- Filtern ----------
PROMPT = """Heute ist {heute}. Du filterst Meldungen für einen Business-Development-Manager. Seine Firma verkauft Übertragungen (Broadcasts) und Wettbewerbs-Events an Hersteller und Veranstalter von Miniatur-Wargames und Sammelkartenspielen (TCG).

Wichtigste Kategorie ist "Chance": Turnier- und Event-Ankündigungen von Herstellern und Veranstaltern weltweit. Dazu zählen: Turniere, Turnierserien, Weltmeisterschaften, Regionals, Meisterschaften, Ligen, Organized Play, Messen mit Turnieren, neue Turnierformate, Medien-, Streaming- und Sponsoring-Partnerschaften und auch, wenn ein Hersteller seine Turnierstruktur oder den Start seiner Turniere ankündigt ("jetzt geht es los, hier kannst du teilnehmen"). Behalte SOLCHE Meldungen immer, auch wenn sie klein sind. Nenne, soweit im Text: wer veranstaltet, was, wann, wo, wie man teilnimmt, und ob eine Übertragung erwähnt wird (sonst "Übertragung nicht erwähnt").

Zweite wichtige Kategorie ist "Neues Spiel": neue Miniatur-Wargames, Skirmish-Spiele und neue TCGs samt Hersteller, mögliche künftige Kunden. Dazu zählen Ankündigungen, Vorbestellungen und Crowdfunding-Kampagnen (Gamefound, Kickstarter), besonders bei Wettbewerbsmodus, bekannten Lizenzpartnern oder hohen Summen. Nenne: Spiel, Hersteller, Lizenzpartner, Wettbewerbs- oder Turniermodus, Stand (Vorbestellung, Kampagne, Release), Preis oder Summe, soweit im Text.

Weitere Kategorien, nur wenn wirklich nützlich:
- "Hersteller": News von Wargame- und TCG-Herstellern, die für Turniere, Wettbewerb, Übertragungen oder das Geschäft wichtig sind, außerdem Geschäftszahlen, Übernahmen und Vertriebsdeals. IMMER behalten: die Wochenübersicht "Games Industry News Roundup".
- "TCG-Trend": nur, wenn Fans oder Medien über ein kleineres oder neues TCG sprechen, das gerade wächst.
- "TCG-Ankündigung": nur für ein neues TCG oder einen neuen Hersteller.
- "Event": sonstige kommende Veranstaltungen.

Games Workshop (Warhammer) und andere große Hersteller: nur Meldungen zu Events, Turnieren, Turnierstruktur, Wettbewerb oder Übertragungen. Neue Sets, Modelle, Regeln und Preise verwerfen. Bei bekannten TCGs: neue Sets, Booster, Promo-Karten, Banlisten und Regeländerungen verwerfen.

Verwirf alles andere, besonders: wer ein Turnier gewonnen hat, Ergebnisse, Siegerlisten, Interviews mit Spielern, Meta-Statistiken und Deck-Anteile, Taktik, Rezensionen, Angebote, Listicles, Turnier-Wochenendberichte, Brettspiele, Rollenspiele, Videospiele, Crowdfunding von Brettspielen, Kunst. Ausnahme: Es geht um ein Wargame oder TCG oder um ein Turnier dafür.
Bereits stattgefundene Turniere verwerfen, außer die Spielerzahl wird genannt und liegt bei 300 oder mehr (zeigt, wie groß der Veranstalter ist).
Verwirf Meldungen, aus denen sich keine konkrete Information ergibt.

Fasse nur zusammen, was in Titel und Text steht. Erfinde nichts. Schreibe nicht auf, was im Text fehlt, außer bei Turnieren zur Übertragung.
Antworte ausschließlich mit einem JSON-Array. Pro behaltene Meldung: {"i": Nummer, "bereich": "tabletop" oder "tcg", "kategorie": "Chance" | "Neues Spiel" | "Hersteller" | "TCG-Trend" | "TCG-Ankündigung" | "Event", "spiel": "Name oder leer", "wichtig": true oder false, "zusammenfassung": "1-2 Sätze auf Deutsch"}.
wichtig ist true für Turnier- und Event-Ankündigungen (Chance), neue Wargames und TCGs (Neues Spiel) und Hersteller-News, die für Wettbewerb, Events oder Übertragungen wichtig sind; sonst false.

Meldungen:
"""

def filtern(items):
    client = Anthropic()
    prompt = PROMPT.replace("{heute}", dt.date.today().isoformat())
    out = []
    for start in range(0, len(items), 40):
        teil = items[start:start + 40]
        liste = "\n".join(f"{i}. {x['title']} | {x['source']} | {x['text']}" for i, x in enumerate(teil))
        try:
            for k in claude_json(client, prompt + liste):
                x = teil[k["i"]]
                out.append({"url": x["url"], "quelle_url": x["url"], "title": x["title"], "source": x["source"],
                            "date": x["date"], "bereich": k["bereich"], "kategorie": k["kategorie"],
                            "spiel": k.get("spiel", ""), "wichtig": bool(k.get("wichtig")),
                            "zusammenfassung": "", "vorschau": x["text"]})
        except Exception as e:
            print("Fehler beim Filtern, Päckchen", start, e)
    print(len(out), "Meldungen behalten")
    return out

# ---------- Zusammenfassen: echter Artikeltext statt nur Titel ----------
def echte_url(url):
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        r = gnewsdecoder(url, interval=1)
        if r.get("status"):
            return r["decoded_url"]
    except Exception as e:
        print("Link nicht auflösbar:", e)
    return url

def seitentext(url, n=3500):
    if "news.google.com" in url:
        return ""
    try:
        h = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) markt-radar"}, timeout=20).text
    except Exception:
        return ""
    h = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|aside)[^>]*>.*?</\1>", " ", h)
    ps = re.findall(r"(?is)<p[^>]*>(.*?)</p>", h)
    t = " ".join(re.sub(r"<[^>]+>", "", p) for p in ps)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()[:n]

ZUSAMMEN_PROMPT = """Schreibe für jede Meldung eine Zusammenfassung auf Deutsch in 2 bis 4 Sätzen: sachlich, klar und verständlich, sodass man ohne den Artikel zu lesen versteht, worum es geht.
Nenne, soweit im Text: bei Turnieren und Events Veranstalter, Termin, Ort, Spiel, Teilnahme oder Anmeldung. Bei neuen Spielen Hersteller, Lizenzpartner, Stand (Vorbestellung, Kampagne, Release), Preis oder Summe und ob es einen Wettbewerbsmodus gibt. Bei Herstellernews: wer, was, warum es wichtig ist.
Nutze nur den Text. Schreibe NIE Sätze darüber, was nicht im Text steht (kein "nicht genannt", "nicht erwähnt", "keine Details"). Gibt der Text zu wenig her, schreibe einen kurzen Satz zu dem, was sicher feststeht. Reicht auch das nicht, gib "" zurück.
Antworte nur mit einem JSON-Array: {"i": Nummer, "zusammenfassung": "..."}.

Meldungen:
"""

def zusammenfassen(items):
    client = Anthropic()
    for x in items:
        x["url"] = echte_url(x["url"])
        t = seitentext(x["url"])
        x["_text"] = t if len(t) >= 300 else x.get("vorschau", "")
    for start in range(0, len(items), 5):
        teil = items[start:start + 5]
        liste = "\n\n".join(f"[{i}] Titel: {x['title']} ({x['source']}, {x['date']})\nText: {x['_text']}" for i, x in enumerate(teil))
        try:
            for k in claude_json(client, ZUSAMMEN_PROMPT + liste, 6000):
                teil[k["i"]]["zusammenfassung"] = (k.get("zusammenfassung") or "").strip()
        except Exception as e:
            print("Fehler beim Zusammenfassen, Päckchen", start, e)
    for x in items:
        x.pop("_text", None)
        x.pop("vorschau", None)
    return items

# ---------- Seiten ohne Feed ----------
SEITEN = [
    ("T³ Turniere (DACH)", "https://www.tabletopturniere.de/", "tabletop"),
    ("Frontline Gaming ITC", "https://www.frontlinegaming.org/itc-battles/", "tabletop"),
    ("Tabletop Battles", "https://www.tabletopbattles.com/", "tabletop"),
    ("Goonhammer Industry News", "https://www.goonhammer.com/tag/industry-news/", "tabletop"),
    ("ICv2 Games", "https://icv2.com/articles/games", "tabletop"),
    ("ICv2 Kalender", "https://icv2.com/articles/calendar", "tabletop"),
    ("Limitless TCG", "https://limitlesstcg.com/", "tcg"),
    ("Limitless Play", "https://play.limitlesstcg.com/", "tcg"),
]

SEITEN_PROMPT = """Heute ist {heute}. Ist die Seite eine Fehlerseite, leer oder zeigt nur Menüpunkte, antworte mit [].
Aus dem Seitentext unten: hole nur ein, was für einen Business-Development-Manager nützlich ist, der Übertragungen und Wettbewerbs-Events an Wargame- und TCG-Hersteller verkauft:
- Event: kommende Turniere, Turnierserien, Organized Play, Meisterschaften (Veranstalter, Ort, Datum, Spiel, Anmeldung)
- Neues Spiel: neue Wargames, Skirmish-Spiele, TCGs mit Hersteller, Stand und Wettbewerbsmodus
- Hersteller: Neuigkeiten zu Wettbewerb, Events, Übertragungen, Partnerschaften, Geschäft. Games Workshop nur dazu.
Verwirf Ergebnislisten, Sieger, Meta-Statistiken (Deck-Anteile), Brettspiele, neue Sets bestehender Spiele und bereits stattgefundene Turniere (außer mit 300 oder mehr Spielern).
Nur Dinge, die im Text stehen. Die URL muss exakt aus den Links im Text stammen (in eckigen Klammern hinter dem Linktext).
Zusammenfassung: 2 bis 4 Sätze auf Deutsch, klar und verständlich. Schreibe nie, was nicht im Text steht.
Antworte nur mit einem JSON-Array: {"title","url","date":"JJJJ-MM-TT oder leer","bereich":"tabletop" oder "tcg","kategorie":"Event" | "Neues Spiel" | "Hersteller","spiel","wichtig":true oder false,"zusammenfassung"}.

Seitentext:
"""

def seiten_lesen():
    from urllib.parse import urljoin
    client = Anthropic()
    prompt = SEITEN_PROMPT.replace("{heute}", dt.date.today().isoformat())
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
            return f" {re.sub(r'<[^>]+>', '', m.group(2))} [{u}] "
        h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", h)
        h = re.sub(r'(?is)<a [^>]*href="([^"#][^"]*)"[^>]*>(.*?)</a>', ersetze, h)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))[:14000]
        if len(text) < 500:
            print(name, ": kaum Text, vermutlich JavaScript-Seite")
            continue
        try:
            for k in claude_json(client, prompt + f"Seite: {name} ({bereich})\n" + text, 6000):
                if k.get("url") in links and k.get("zusammenfassung"):
                    out.append({"url": k["url"], "quelle_url": k["url"], "title": k["title"], "source": name,
                                "date": k.get("date") or dt.date.today().isoformat(),
                                "bereich": k.get("bereich", bereich), "kategorie": k.get("kategorie", "Event"),
                                "spiel": k.get("spiel", ""), "wichtig": bool(k.get("wichtig")),
                                "zusammenfassung": k["zusammenfassung"]})
        except Exception as e:
            print("Fehler beim Lesen von", name, e)
    print(len(out), "Einträge aus Seiten")
    return out

# ---------- Buzz: welche Spiele kommen wie oft vor ----------
BUZZ_PROMPT = """Nenne pro Meldung die konkreten Wargames, Miniaturenspiele, Sammelkartenspiele und Kartenspiele, um die es geht. Verwende einheitliche englische Namen, z. B. "Pokémon TCG", "Magic: The Gathering", "Warhammer 40,000", "Riftbound". Keine Firmen, keine Videospiele, keine Brettspiele. Bereich b: "tabletop" (Wargames, Miniaturen) oder "tcg" (Sammelkarten- und Kartenspiele).
Antworte nur mit einem JSON-Array: {"i": Nummer, "spiele": [{"n": "Name", "b": "tabletop" oder "tcg"}]}. Meldungen ohne passendes Spiel weglassen.

Meldungen:
"""

def buzz_zaehlen(roh):
    client = Anthropic()
    zaehler = {"tabletop": Counter(), "tcg": Counter()}
    namen = {}
    for start in range(0, len(roh), 60):
        teil = roh[start:start + 60]
        liste = "\n".join(f"{i}. {x['title']}" for i, x in enumerate(teil))
        try:
            for k in claude_json(client, BUZZ_PROMPT + liste):
                for b, n in {(s["b"], s["n"].strip()) for s in k.get("spiele", [])}:
                    if b in zaehler and n:
                        namen.setdefault(n.lower(), n)
                        zaehler[b][n.lower()] += 1
        except Exception as e:
            print("Fehler beim Zählen, Päckchen", start, e)
    return {b: {namen[k]: v for k, v in z.items()} for b, z in zaehler.items()}

def buzz_bauen(zaehlung, verlauf):
    heute = dt.date.today()
    verlauf[heute.isoformat()] = zaehlung
    for d in list(verlauf):
        if d < (heute - dt.timedelta(days=60)).isoformat():
            del verlauf[d]
    vorher = [d for d in verlauf if d <= (heute - dt.timedelta(days=6)).isoformat()]
    alt = verlauf[max(vorher)] if vorher else None
    out = {"datum": heute.isoformat(), "vergleich": max(vorher) if vorher else None}
    for b in ("tabletop", "tcg"):
        liste = sorted(zaehlung.get(b, {}).items(), key=lambda kv: -kv[1])[:25]
        out[b] = [{"n": n, "c": c, "v": (alt.get(b, {}).get(n, 0) if alt else None)} for n, c in liste]
    return out

# ---------- Entdoppeln ----------
def entdoppeln(items):
    """Gleiche Meldungen aus mehreren Quellen zusammenführen; wichtige haben Vorrang."""
    out, gesehen = [], []
    for x in sorted(items, key=lambda x: not x.get("wichtig")):
        t = re.sub(r"\W+", " ", x["title"].lower()).strip()
        doppelt = False
        for d, q, u in gesehen:
            if d != x["date"]:
                continue
            if difflib.SequenceMatcher(None, t, u).ratio() >= (0.95 if q == x["source"] else 0.55):
                doppelt = True
                break
        if not doppelt:
            gesehen.append((x["date"], x["source"], t))
            out.append(x)
    return out

# ---------- Morgenbrief ----------
TAGE_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]

BRIEF_PROMPT = """Schreibe den Morgenbrief für Gerrit. Er arbeitet im Business Development bei einem Unternehmen, das Übertragungen (Broadcasts) und Wettbewerbs-Events an Hersteller und Veranstalter von Miniatur-Wargames und Sammelkartenspielen verkauft. Er liest den Brief morgens beim Kaffee oder lässt ihn sich vorlesen.

Wichtig für Gerrit: Turnier- und Event-Ankündigungen von Herstellern und Veranstaltern weltweit, Turnierstrukturen und Organized Play, neue Wargames, Skirmish-Spiele und TCGs mit ihren Herstellern, und Hersteller-Neuigkeiten, die für Wettbewerb, Events oder Übertragungen wichtig sind. Darüber will er alles wissen, immer. Nicht interessant: wer gewonnen hat, Ergebnisse, Meta-Statistiken, neue Sets bestehender Spiele, Brettspiele.

Ton: freundlich, klar und ruhig, wie ein netter Kollege, der beim Kaffee kurz berichtet. Du-Form. Kurze Sätze, einfache Wörter. Wenige englische Begriffe.
Form für Vorlesen: reiner Fließtext in kurzen Absätzen, durch Leerzeilen getrennt. Keine Aufzählungszeichen, keine Überschriften mit Sonderzeichen, keine Links, keine Klammern, keine Emojis.
Aufbau:
1. Begrüßung mit dem heutigen Datum: {datum}.
2. Turniere und Events weltweit: alle wichtigen Meldungen, nichts auslassen. Nenne Veranstalter, was, wann, wo und wie man teilnimmt. Zuerst Wargames, dann TCG, zuerst das, was bald stattfindet.
3. Neue Spiele und Hersteller: Spiel, Hersteller, Lizenzpartner, Stand und Wettbewerbsmodus. Sag in einem Satz, warum der Hersteller ein möglicher Kunde werden könnte, nur wenn die Meldung das hergibt.
4. Neuigkeiten der Hersteller, die für Wettbewerb, Events und Übertragungen wichtig sind. Kurz.
5. Worüber gesprochen wird: nenne anhand der Liste "Medienpräsenz" die Spitzenreiter und besonders kleinere oder neue Spiele, die auffallen, bei Tabletop und bei TCG getrennt. Gibt es einen Vergleich zur Vorwoche, nenne Aufsteiger. Sag einmal kurz, dass das Zählungen aus Nachrichtenquellen sind und keine Gespräche von Fans.
6. Ein freundlicher Abschluss.
Regeln: Nutze ausschließlich Fakten aus den Meldungen unten, erfinde nichts, und nenne die Quelle im Satz (zum Beispiel: laut Wargamer). Gib nur Daten und Zahlen an, die dort stehen. Schreibe nie Sätze darüber, was in den Meldungen fehlt. Ist wenig los, sag das ehrlich und kurz. Länge: etwa 400 bis 600 Wörter.

Medienpräsenz (Anzahl Meldungen in den letzten 7 Tagen):
{buzz}

Meldungen der letzten Tage:
"""

def buzz_text(buzz):
    zeilen = []
    for b, titel in (("tabletop", "Tabletop"), ("tcg", "TCG")):
        teile = []
        for g in (buzz.get(b) or [])[:10]:
            v = "" if g["v"] is None else f", Vorwoche {g['v']}"
            teile.append(f"{g['n']} ({g['c']}{v})")
        zeilen.append(f"{titel}: " + "; ".join(teile))
    return "\n".join(zeilen)

def morgenbrief(items, buzz):
    heute = dt.date.today()
    grenze = (heute - dt.timedelta(days=7)).isoformat()
    frisch = [x for x in items if x["date"] >= grenze]
    auswahl = [x for x in frisch if x.get("wichtig")][:40] + [x for x in frisch if not x.get("wichtig")][:10]
    if not auswahl:
        return None
    liste = "\n".join(f"- [{x['bereich']}, {x['kategorie']}] {x['title']} ({x['source']}, {x['date']}): {x['zusammenfassung']}" for x in auswahl)
    datum = f"{TAGE_DE[heute.weekday()]}, {heute.day}. {MONATE[heute.month - 1]} {heute.year}"
    prompt = BRIEF_PROMPT.replace("{datum}", datum).replace("{buzz}", buzz_text(buzz or {})) + liste
    r = Anthropic().messages.create(model=MODELL_GROSS, max_tokens=6000, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if b.type == "text").strip()

# ---------- Ablauf ----------
def main():
    try:
        alt_daten = json.load(open("data.json", encoding="utf-8"))
    except Exception:
        alt_daten = {}
    alt = alt_daten.get("items", [])
    bekannt = {x.get("quelle_url", x["url"]) for x in alt} | {x["url"] for x in alt}

    roh = sammeln()
    behalten = [x for x in filtern(roh) if x["url"] not in bekannt]
    print(len(behalten), "davon neu")
    neu = zusammenfassen(behalten) + [x for x in seiten_lesen() if x["url"] not in bekannt]

    grenze = (dt.date.today() - dt.timedelta(days=BEHALTEN)).isoformat()
    alle = {x["url"]: x for x in alt + neu if x["date"] >= grenze}
    items = sorted(entdoppeln(list(alle.values())), key=lambda x: x["date"], reverse=True)

    verlauf = alt_daten.get("buzz_verlauf", {})
    buzz = alt_daten.get("buzz")
    try:
        buzz = buzz_bauen(buzz_zaehlen(roh), verlauf)
    except Exception as e:
        print("Fehler bei der Buzz-Auswertung", e)

    brief = alt_daten.get("brief")
    try:
        text = morgenbrief(items, buzz)
        if text:
            brief = {"text": text, "datum": dt.date.today().isoformat()}
    except Exception as e:
        print("Fehler beim Morgenbrief", e)

    json.dump({"stand": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "brief": brief,
               "buzz": buzz, "buzz_verlauf": verlauf, "items": items},
              open("data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(neu), "neu,", len(items), "gesamt")

main()
