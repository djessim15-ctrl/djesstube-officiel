# -*- coding: utf-8 -*-
"""
DjessTube — YouTube en mieux, en local.
- Recherche sur tout YouTube (via yt-dlp)
- Lecture 1080p sans pub (flux directs, pas le lecteur YouTube)
- Téléchargements fiables (1080p / 720p / MP3) avec reprise sur erreur
- Sous-titres nettoyés (fini les doublons des sous-titres auto) + traduction FR hors ligne

Lancer :  python server.py   puis ouvrir http://localhost:8765
"""
import glob
import json
import os
import re
import subprocess
import threading

from flask import Flask, jsonify, request, send_from_directory, Response

BASE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.join(BASE, "telechargements")
SUBS_CACHE = os.path.join(BASE, "cache_soustitres")
os.makedirs(DOWNLOADS, exist_ok=True)
os.makedirs(SUBS_CACHE, exist_ok=True)

app = Flask(__name__, static_folder=BASE, static_url_path="")

# ---------------------------------------------------------------- traduction
_translate_lock = threading.Lock()

def ensure_translator(src="en", dst="fr"):
    """Installe le paquet de traduction hors ligne si absent (1 seule fois)."""
    import argostranslate.package, argostranslate.translate
    with _translate_lock:
        codes = [l.code for l in argostranslate.translate.get_installed_languages()]
        if src in codes and dst in codes:
            return True
        try:
            argostranslate.package.update_package_index()
            for pkg in argostranslate.package.get_available_packages():
                if pkg.from_code == src and pkg.to_code == dst:
                    argostranslate.package.install_from_path(pkg.download())
                    return True
        except Exception:
            return False
    return False

def translate_text(text, src="en", dst="fr"):
    import argostranslate.translate
    return argostranslate.translate.translate(text, src, dst)

# ------------------------------------------------------------------- yt-dlp
def ytdlp_json(args):
    proc = subprocess.run(
        ["yt-dlp", "--no-warnings", "-j"] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    items = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return items

@app.route("/api/recherche")
def recherche():
    """?page=0,1,2… pour le défilement infini (20 résultats par page)."""
    q = request.args.get("q", "").strip()
    n = min(int(request.args.get("n", 20)), 30)
    page = max(int(request.args.get("page", 0)), 0)
    if not q:
        return jsonify([])
    debut, fin = page * n + 1, (page + 1) * n
    items = ytdlp_json(["--flat-playlist", "--playlist-items", f"{debut}:{fin}",
                        f"ytsearch{fin}:{q}"])
    results = []
    for it in items:
        results.append({
            "id": it.get("id"),
            "titre": it.get("title"),
            "chaine": it.get("channel") or it.get("uploader"),
            "duree": it.get("duration"),
            "vues": it.get("view_count"),
            "miniature": f"https://i.ytimg.com/vi/{it.get('id')}/mqdefault.jpg",
        })
    return jsonify(results)

@app.route("/api/video/<vid>")
def video_info(vid):
    """URL de flux directs (sans pub) + liste des sous-titres."""
    items = ytdlp_json([f"https://www.youtube.com/watch?v={vid}"])
    if not items:
        return jsonify({"erreur": "Vidéo introuvable"}), 404
    it = items[0]
    best = None      # format combiné (secours, souvent 360p)
    best_v = None    # meilleur flux vidéo seul h264 <=1080p
    best_a = None    # meilleur flux audio m4a
    for f in it.get("formats", []):
        if not f.get("protocol", "").startswith("http") or f.get("protocol", "").startswith("http_dash"):
            continue
        vc, ac = f.get("vcodec", "none"), f.get("acodec", "none")
        h = f.get("height") or 0
        if vc != "none" and ac != "none":
            if best is None or h > (best.get("height") or 0):
                best = f
        elif vc.startswith("avc1") and ac == "none" and h <= 1080 and f.get("ext") == "mp4":
            if best_v is None or h > (best_v.get("height") or 0):
                best_v = f
        elif vc == "none" and ac.startswith("mp4a"):
            if best_a is None or (f.get("abr") or 0) > (best_a.get("abr") or 0):
                best_a = f
    subs = {}
    for lang in (it.get("subtitles") or {}):
        subs[lang] = "manuel"
    for lang in (it.get("automatic_captions") or {}):
        if lang not in subs and "-" not in lang:
            subs[lang] = "auto"
    return jsonify({
        "id": vid,
        "titre": it.get("title"),
        "chaine": it.get("channel"),
        "abonnes": it.get("channel_follower_count"),
        "vues": it.get("view_count"),
        "likes": it.get("like_count"),
        "date": it.get("upload_date"),
        "description": (it.get("description") or "")[:1500],
        "flux": best.get("url") if best else None,
        "qualite": f"{best.get('height')}p" if best else None,
        "flux_video": best_v.get("url") if best_v else None,
        "flux_audio": best_a.get("url") if (best_v and best_a) else None,
        "qualite_hd": f"{best_v.get('height')}p" if best_v else None,
        "sous_titres": subs,
    })

# ------------------------------------------------------------ sous-titres
TAG = re.compile(r"<[^>]+>")

def nettoyer_vtt(vtt):
    """Nettoie les sous-titres auto YouTube : balises karaoké, lignes dupliquées
    d'un bloc à l'autre, blocs vides. C'est ça qui faisait « buguer » l'affichage."""
    blocs = re.split(r"\n\n+", vtt.strip())
    cues = []
    lignes_prec = []
    for bloc in blocs:
        lignes = bloc.splitlines()
        if not lignes or "-->" not in bloc:
            continue
        idx = next(i for i, l in enumerate(lignes) if "-->" in l)
        timing = lignes[idx].split(" align:")[0].split(" position:")[0].strip()
        texte_lignes = []
        for l in lignes[idx + 1:]:
            l = TAG.sub("", l).replace("​", "").strip()
            l = re.sub(r"\s+", " ", l)
            if l and l not in lignes_prec and l not in texte_lignes:
                texte_lignes.append(l)
        if not texte_lignes:
            continue
        lignes_prec = texte_lignes
        cues.append((timing, "\n".join(texte_lignes)))
    out = ["WEBVTT", ""]
    for timing, texte in cues:
        out += [timing, texte, ""]
    return "\n".join(out)

def telecharger_vtt(vid, lang):
    tmp = os.path.join(SUBS_CACHE, f".dl_{vid}")
    subprocess.run(
        ["yt-dlp", "--no-warnings", "--skip-download",
         "--write-subs", "--write-auto-subs", "--sub-langs", lang,
         "--sub-format", "vtt", "-o", tmp,
         f"https://www.youtube.com/watch?v={vid}"],
        capture_output=True,
    )
    path = f"{tmp}.{lang}.vtt"
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        vtt = f.read()
    os.remove(path)
    return vtt

_subs_locks = {}
def _sub_lock(key):
    return _subs_locks.setdefault(key, threading.Lock())

@app.route("/api/soustitres/<vid>")
def soustitres(vid):
    """Sous-titres VTT nettoyés. ?lang=en&traduire=1 => traduits en FR hors ligne.
    Cache disque : instantané dès la 2e fois, et disponible hors ligne."""
    lang = request.args.get("lang", "en")
    traduire = request.args.get("traduire") == "1" and lang != "fr"
    safe_lang = re.sub(r"[^a-zA-Z-]", "", lang)
    cache = os.path.join(SUBS_CACHE, f"{vid}_{safe_lang}{'_fr' if traduire else ''}.vtt")
    with _sub_lock(cache):
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                return Response(f.read(), mimetype="text/vtt")
        vtt = telecharger_vtt(vid, lang)
        if vtt is None:
            return Response("WEBVTT\n", mimetype="text/vtt")
        vtt = nettoyer_vtt(vtt)
        if traduire and ensure_translator(lang, "fr"):
            out = []
            for line in vtt.splitlines():
                s = line.strip()
                if not s or s == "WEBVTT" or "-->" in s:
                    out.append(line)
                else:
                    try:
                        out.append(translate_text(s, lang, "fr"))
                    except Exception:
                        out.append(s)
            vtt = "\n".join(out)
        with open(cache, "w", encoding="utf-8") as f:
            f.write(vtt)
    return Response(vtt, mimetype="text/vtt")

# --------------------------------------------------------------- téléchargement
_downloads = {}  # vid -> {"etat", "pct", "titre", "qualite"}

FORMATS = {
    "1080": "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "720":  "bestvideo[height<=720][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "audio": "bestaudio/best",
}

def _nettoyer_fragments(vid):
    """Supprime les fragments partiels/corrompus (cause n°1 des téléchargements ratés)."""
    for pattern in (f"*[[]{vid}[]]*.part", f"*[[]{vid}[]].f*"):
        for p in glob.glob(os.path.join(DOWNLOADS, pattern)):
            try:
                os.remove(p)
            except OSError:
                pass

def _cmd_download(vid, qualite):
    cmd = ["yt-dlp", "--no-warnings", "--newline", "--windows-filenames",
           "--force-overwrites",
           "-f", FORMATS.get(qualite, FORMATS["1080"]),
           "-o", os.path.join(DOWNLOADS, "%(title).150s [%(id)s].%(ext)s"),
           f"https://www.youtube.com/watch?v={vid}"]
    if qualite == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    else:
        cmd += ["--merge-output-format", "mp4",
                "--write-subs", "--write-auto-subs", "--sub-langs", "fr,en",
                "--convert-subs", "srt"]
    return cmd

def _download_worker(vid, qualite, titre):
    _downloads[vid] = {"etat": "en cours", "pct": 0, "titre": titre, "qualite": qualite}
    for tentative in range(2):  # 2e essai après nettoyage si échec
        _nettoyer_fragments(vid)
        proc = subprocess.Popen(
            _cmd_download(vid, qualite),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for line in proc.stdout:
            m = re.search(r"\[download\]\s+([\d.]+)%", line)
            if m:
                _downloads[vid]["pct"] = float(m.group(1))
        proc.wait()
        if proc.returncode == 0:
            _nettoyer_fragments(vid)
            _downloads[vid].update(etat="terminé", pct=100)
            return
    _nettoyer_fragments(vid)
    _downloads[vid].update(etat="erreur")

@app.route("/api/telecharger/<vid>", methods=["POST"])
def telecharger(vid):
    if _downloads.get(vid, {}).get("etat") == "en cours":
        return jsonify(_downloads[vid])
    data = request.get_json(silent=True) or {}
    threading.Thread(
        target=_download_worker,
        args=(vid, str(data.get("qualite", "1080")), data.get("titre", vid)),
        daemon=True,
    ).start()
    return jsonify({"etat": "en cours", "pct": 0})

@app.route("/api/telechargements")
def telechargements():
    return jsonify(_downloads)

@app.route("/api/bibliotheque")
def bibliotheque():
    """Vidéos/musiques déjà téléchargées = utilisables sans Internet."""
    vids = []
    for f in sorted(os.listdir(DOWNLOADS), key=lambda f: -os.path.getmtime(os.path.join(DOWNLOADS, f))):
        low = f.lower()
        if low.endswith((".mp4", ".mkv", ".webm", ".mp3", ".m4a")):
            m = re.search(r"\[([A-Za-z0-9_-]{11})\]", f)
            vids.append({
                "fichier": f,
                "titre": re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]", "", os.path.splitext(f)[0]),
                "id": m.group(1) if m else None,
                "audio": low.endswith((".mp3", ".m4a")),
                "taille": os.path.getsize(os.path.join(DOWNLOADS, f)),
            })
    return jsonify(vids)

@app.route("/api/supprimer", methods=["POST"])
def supprimer():
    nom = (request.get_json(silent=True) or {}).get("fichier", "")
    path = os.path.abspath(os.path.join(DOWNLOADS, nom))
    if not path.startswith(os.path.abspath(DOWNLOADS)) or not os.path.isfile(path):
        return jsonify({"ok": False}), 400
    os.remove(path)
    base = os.path.splitext(path)[0]
    for srt in glob.glob(glob.escape(base) + "*.srt"):
        os.remove(srt)
    return jsonify({"ok": True})

@app.route("/api/fichier/<path:nom>")
def fichier(nom):
    return send_from_directory(DOWNLOADS, nom)

@app.route("/api/soustitres-locaux/<vid>")
def soustitres_locaux(vid):
    """Sous-titres .srt d'une vidéo téléchargée, convertis en VTT (100% hors ligne)."""
    for lang in ("fr", "en"):
        for p in glob.glob(os.path.join(DOWNLOADS, f"*[[]{vid}[]].{lang}.srt")):
            with open(p, encoding="utf-8", errors="replace") as f:
                srt = f.read()
            vtt = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt)
            vtt = re.sub(r"^\d+\s*$", "", vtt, flags=re.M)
            return Response(vtt, mimetype="text/vtt")
    return Response("WEBVTT\n", mimetype="text/vtt")

@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")

if __name__ == "__main__":
    print("DjessTube -> http://localhost:8765")
    threading.Thread(target=ensure_translator, daemon=True).start()
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
