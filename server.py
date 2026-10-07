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
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, request, send_from_directory, Response
from werkzeug.exceptions import HTTPException

BASE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.join(BASE, "telechargements")
SUBS_CACHE = os.path.join(BASE, "cache_soustitres")
os.makedirs(DOWNLOADS, exist_ok=True)
os.makedirs(SUBS_CACHE, exist_ok=True)

app = Flask(__name__, static_folder=BASE, static_url_path="")

VERSION = "1.1"


def _env_int(nom, defaut):
    try:
        return max(5, int(os.environ.get(nom, defaut)))
    except (TypeError, ValueError):
        return defaut


# Délais maxi : sans eux, un yt-dlp qui bloque laisse la page charger… à l'infini.
TIMEOUT_RECHERCHE = _env_int("DJESSTUBE_TIMEOUT_RECHERCHE", 90)
TIMEOUT_VIDEO = _env_int("DJESSTUBE_TIMEOUT_VIDEO", 90)
TIMEOUT_SUBS = _env_int("DJESSTUBE_TIMEOUT_SOUSTITRES", 60)
TIMEOUT_STALL_DL = _env_int("DJESSTUBE_TIMEOUT_TELECHARGEMENT", 180)  # téléchargement bloqué
ID_VALIDE = re.compile(r"^[A-Za-z0-9_-]{11}$")


# --------------------------------------------------------------- erreurs propres
class ErreurApi(Exception):
    """Erreur renvoyée en JSON (jamais une page HTML) avec un message lisible."""

    def __init__(self, message, status=502, code="erreur"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@app.errorhandler(ErreurApi)
def _erreur_api(e):
    return jsonify({"erreur": e.message, "code": e.code}), e.status


@app.errorhandler(Exception)
def _erreur_generale(e):
    """Toute exception sur une route /api/* doit sortir en JSON lisible.

    Avant, Flask renvoyait une page HTML « 500 Internal Server Error » :
    le navigateur n'arrivait pas à la lire et le chargement ne s'arrêtait jamais.
    """
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/") and e.code >= 500:
            return jsonify({"erreur": f"Erreur serveur ({e.code}).", "code": "http"}), e.code
        return e
    app.logger.exception("Erreur non gérée")
    if request.path.startswith("/api/"):
        return jsonify({
            "erreur": "Erreur interne de DjessTube : " + str(e)[:300],
            "code": "interne",
        }), 500
    return "Erreur interne — regarde la fenêtre noire de DjessTube.", 500


# ------------------------------------------------------------------- yt-dlp
_ytdlp_cmd = None
_ytdlp_verifie = False
_ytdlp_version = None


def ytdlp_cmd():
    """Commande à utiliser pour yt-dlp (exécutable, sinon module Python). None si absent."""
    global _ytdlp_cmd, _ytdlp_verifie, _ytdlp_version
    if _ytdlp_verifie:
        return _ytdlp_cmd
    _ytdlp_verifie = True

    # 1) dans le PATH, 2) à côté de python.exe (Windows : Scripts\yt-dlp.exe)
    candidats = [shutil.which("yt-dlp")]
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidats += [
        os.path.join(exe_dir, "yt-dlp.exe"),
        os.path.join(exe_dir, "yt-dlp"),
        os.path.join(exe_dir, "Scripts", "yt-dlp.exe"),
    ]
    for c in candidats:
        if c and os.path.isfile(c):
            _ytdlp_cmd = [c]
            break

    # 3) installé en bibliothèque Python ? (python -m yt_dlp)
    if _ytdlp_cmd is None:
        try:
            r = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                _ytdlp_cmd = [sys.executable, "-m", "yt_dlp"]
                _ytdlp_version = (r.stdout or "").strip() or None
        except Exception:
            pass
    return _ytdlp_cmd


def ytdlp_version():
    """Version de yt-dlp (pour savoir si elle est trop vieille)."""
    global _ytdlp_version
    if _ytdlp_version:
        return _ytdlp_version
    cmd = ytdlp_cmd()
    if not cmd:
        return None
    try:
        # Délai court : ce test ne doit jamais retarder l'affichage du site.
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=8)
        premiere = ((r.stdout or "").strip().splitlines() or [""])[0].strip()
        _ytdlp_version = premiere[:40] or None
    except Exception:
        _ytdlp_version = None
    return _ytdlp_version


MSG_PAS_INSTALLE = ("yt-dlp n'est pas installé sur cet ordinateur. "
                    "Ouvre une invite de commandes et tape :  pip install -U yt-dlp")

# Messages de yt-dlp qui veulent dire « mets-le à jour »
_MOTIFS_MAJ = (
    "unable to extract", "nsig extraction failed", "signature extraction failed",
    "failed to extract any player response", "player response",
    "some web client https formats have been skipped", "unable to download webpage",
)


def _expliquer_erreur_ytdlp(stderr):
    """Transforme la sortie d'yt-dlp en conseil compréhensible."""
    bas = (stderr or "").lower()
    if any(m in bas for m in _MOTIFS_MAJ):
        return ("YouTube a changé quelque chose : ta version de yt-dlp est trop ancienne. "
                "Mets-la à jour :  pip install -U yt-dlp")
    if "sign in to confirm" in bas or "not a bot" in bas:
        return ("YouTube demande une confirmation (« je ne suis pas un robot ») depuis cette connexion. "
                "Réessaie plus tard, ou mets yt-dlp à jour.")
    if "private video" in bas or "video unavailable" in bas:
        return "Cette vidéo n'est pas accessible (privée, supprimée ou bloquée dans ton pays)."
    if "timed out" in bas or "timeout" in bas or "connection" in bas:
        return "Connexion à YouTube trop lente ou interrompue. Vérifie Internet puis réessaie."
    ligne = next((l.strip() for l in reversed((stderr or "").splitlines())
                  if "ERROR" in l or "error" in l), "")
    return ligne[:300] if ligne else "yt-dlp n'a rien pu récupérer."


def ytdlp_json(args, timeout=TIMEOUT_VIDEO, vide_ok=True):
    """Lance yt-dlp -j et renvoie la liste des objets JSON.

    Lève ErreurApi (message clair, statut HTTP) au lieu de planter en 500 :
    c'est ce qui faisait tourner le rond de chargement pour toujours.
    """
    cmd = ytdlp_cmd()
    if not cmd:
        raise ErreurApi(MSG_PAS_INSTALLE, 503, "yt-dlp-absent")
    ligne_cmd = cmd + ["--no-warnings", "--socket-timeout", "15", "--retries", "2", "-j"] + args
    try:
        proc = subprocess.run(ligne_cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ErreurApi(
            f"yt-dlp ne répond pas (plus de {timeout} s). "
            "Vérifie ta connexion Internet puis réessaie.", 504, "timeout")
    except FileNotFoundError:
        raise ErreurApi(MSG_PAS_INSTALLE, 503, "yt-dlp-absent")
    except Exception as e:                                    # noqa: BLE001
        raise ErreurApi("Impossible de lancer yt-dlp : " + str(e)[:200], 500, "lancement")

    items = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not items:
        if not vide_ok and proc.returncode != 0:
            raise ErreurApi(_expliquer_erreur_ytdlp(proc.stderr), 502, "yt-dlp")
        if proc.returncode != 0 and proc.stderr.strip():
            raise ErreurApi(_expliquer_erreur_ytdlp(proc.stderr), 502, "yt-dlp")
    return items


def _entier(valeur, defaut, mini, maxi):
    try:
        return max(mini, min(maxi, int(valeur)))
    except (TypeError, ValueError):
        return defaut


def _verifier_id(vid):
    if not ID_VALIDE.match(vid or ""):
        raise ErreurApi("Identifiant de vidéo invalide.", 400, "id")
    return vid


# ------------------------------------------------------- cache recherche (10 min)
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 600
CACHE_MAX = 250


def cache_lire(cle):
    with _cache_lock:
        entree = _cache.get(cle)
        if not entree:
            return None
        date, valeur = entree
        if time.time() - date > CACHE_TTL:
            _cache.pop(cle, None)
            return None
        return valeur


def cache_ecrire(cle, valeur):
    with _cache_lock:
        if len(_cache) > CACHE_MAX:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:CACHE_MAX // 2]:
                _cache.pop(k, None)
        _cache[cle] = (time.time(), valeur)


# ---------------------------------------------------------------- traduction
_translate_lock = threading.Lock()
_trad_etat = {"dispo": False, "en_cours": False, "message": "traduction hors ligne non vérifiée"}


def traduction_prete(src="en", dst="fr"):
    """Vérifie SANS bloquer si le paquet de traduction est installé."""
    try:
        import argostranslate.translate
        codes = [l.code for l in argostranslate.translate.get_installed_languages()]
        ok = src in codes and dst in codes
        _trad_etat.update(dispo=ok, en_cours=False,
                          message="prête" if ok else "paquet de traduction non installé")
        return ok
    except ImportError:
        _trad_etat.update(dispo=False, en_cours=False,
                          message="argostranslate n'est pas installé (pip install argostranslate)")
        return False
    except Exception as e:                                    # noqa: BLE001
        _trad_etat.update(dispo=False, en_cours=False, message="erreur : " + str(e)[:120])
        return False


def preparer_traduction(src="en", dst="fr"):
    """Installe le paquet de traduction en arrière-plan (long : ~100 Mo).

    Avant, cette installation se faisait DANS la requête HTTP : le navigateur
    attendait plusieurs minutes et la page semblait bloquée à l'infini.
    """
    global _trad_etat
    if _trad_etat.get("en_cours"):
        return
    try:
        import argostranslate.package, argostranslate.translate  # noqa: F401
    except ImportError:
        _trad_etat.update(dispo=False, en_cours=False,
                          message="argostranslate n'est pas installé (pip install argostranslate)")
        return
    if traduction_prete(src, dst):
        return
    with _translate_lock:
        if _trad_etat.get("en_cours"):
            return
        _trad_etat.update(en_cours=True, message="téléchargement du paquet de traduction…")
        try:
            import argostranslate.package
            argostranslate.package.update_package_index()
            for pkg in argostranslate.package.get_available_packages():
                if pkg.from_code == src and pkg.to_code == dst:
                    argostranslate.package.install_from_path(pkg.download())
                    break
            _trad_etat.update(en_cours=False, message="prête")
            traduction_prete(src, dst)
        except Exception as e:                                # noqa: BLE001
            _trad_etat.update(en_cours=False, message="échec : " + str(e)[:150])


def translate_text(text, src="en", dst="fr"):
    import argostranslate.translate
    return argostranslate.translate.translate(text, src, dst)


# ------------------------------------------------------------------ /api/etat
@app.route("/api/etat")
def etat():
    """État de l'installation — le navigateur s'en sert pour prévenir l'utilisateur."""
    cmd = ytdlp_cmd()
    return jsonify({
        "version": VERSION,
        "yt_dlp": cmd is not None,
        "yt_dlp_version": ytdlp_version() if cmd else None,
        "yt_dlp_commande": " ".join(cmd) if cmd else None,
        "message": None if cmd else MSG_PAS_INSTALLE,
        "traduction": dict(_trad_etat),
    })


# ------------------------------------------------------------------ recherche
@app.route("/api/recherche")
def recherche():
    """?page=0,1,2… pour le défilement infini (20 résultats par page)."""
    q = request.args.get("q", "").strip()
    n = _entier(request.args.get("n"), 20, 1, 30)
    page = _entier(request.args.get("page"), 0, 0, 100)
    if not q:
        return jsonify([])

    cle = (q.lower(), page, n)
    en_cache = cache_lire(cle)
    if en_cache is not None:
        return jsonify(en_cache)

    debut, fin = page * n + 1, (page + 1) * n
    items = ytdlp_json(["--flat-playlist",
                        "--playlist-items", f"{debut}:{fin}",
                        f"ytsearch{fin}:{q}"],
                       timeout=TIMEOUT_RECHERCHE)
    resultats = []
    for it in items:
        resultats.append({
            "id": it.get("id"),
            "titre": it.get("title"),
            "chaine": it.get("channel") or it.get("uploader"),
            "duree": it.get("duration"),
            "vues": it.get("view_count"),
            "miniature": f"https://i.ytimg.com/vi/{it.get('id')}/mqdefault.jpg",
        })
    if resultats:
        cache_ecrire(cle, resultats)
    return jsonify(resultats)


@app.route("/api/video/<vid>")
def video_info(vid):
    """URL de flux directs (sans pub) + liste des sous-titres."""
    _verifier_id(vid)
    items = ytdlp_json(["--no-playlist", f"https://www.youtube.com/watch?v={vid}"],
                       timeout=TIMEOUT_VIDEO, vide_ok=False)
    if not items:
        raise ErreurApi("Vidéo introuvable (ou yt-dlp n'a pas pu la lire).", 404, "introuvable")
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
    cmd = ytdlp_cmd()
    if not cmd:
        raise ErreurApi(MSG_PAS_INSTALLE, 503, "yt-dlp-absent")
    lang = re.sub(r"[^a-zA-Z,\-]", "", lang)
    tmp = os.path.join(SUBS_CACHE, f".dl_{vid}")
    try:
        subprocess.run(
            cmd + ["--no-warnings", "--skip-download", "--socket-timeout", "15",
                   "--write-subs", "--write-auto-subs", "--sub-langs", lang,
                   "--sub-format", "vtt", "-o", tmp,
                   f"https://www.youtube.com/watch?v={vid}"],
            capture_output=True, timeout=TIMEOUT_SUBS,
        )
    except subprocess.TimeoutExpired:
        raise ErreurApi("Les sous-titres mettent trop de temps à arriver.", 504, "timeout")
    path = f"{tmp}.{lang}.vtt"
    if not os.path.exists(path):                 # yt-dlp nomme parfois autrement
        trouves = glob.glob(glob.escape(tmp) + ".*.vtt")
        path = trouves[0] if trouves else None
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        vtt = f.read()
    try:
        os.remove(path)
    except OSError:
        pass
    return vtt


_subs_locks = {}


def _sub_lock(key):
    return _subs_locks.setdefault(key, threading.Lock())


@app.route("/api/soustitres/<vid>")
def soustitres(vid):
    """Sous-titres VTT nettoyés. ?lang=en&traduire=1 => traduits en FR hors ligne.
    Cache disque : instantané dès la 2e fois, et disponible hors ligne."""
    _verifier_id(vid)
    lang = request.args.get("lang", "en")
    traduire = request.args.get("traduire") == "1" and lang != "fr"
    safe_lang = re.sub(r"[^a-zA-Z-]", "", lang)
    cache = os.path.join(SUBS_CACHE, f"{vid}_{safe_lang}{'_fr' if traduire else ''}.vtt")
    entete = {}
    with _sub_lock(cache):
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                return Response(f.read(), mimetype="text/vtt")
        vtt = telecharger_vtt(vid, lang)
        if vtt is None:
            return Response("WEBVTT\n", mimetype="text/vtt",
                            headers={"X-Soustitres": "indisponibles"})
        vtt = nettoyer_vtt(vtt)
        if traduire:
            # On ne bloque JAMAIS la page : si le paquet de traduction n'est pas
            # prêt, on renvoie la version originale tout de suite avec un message.
            if traduction_prete(lang, "fr"):
                out = []
                for line in vtt.splitlines():
                    s = line.strip()
                    if not s or s == "WEBVTT" or "-->" in s:
                        out.append(line)
                    else:
                        try:
                            out.append(translate_text(s, lang, "fr"))
                        except Exception:                 # noqa: BLE001
                            out.append(s)
                vtt = "\n".join(out)
            else:
                threading.Thread(target=preparer_traduction, args=(lang, "fr"),
                                 daemon=True).start()
                # Uniquement du texte ASCII : les entêtes HTTP n'acceptent pas les accents.
                entete["X-Traduction"] = ("absente" if "argostranslate" in _trad_etat.get("message", "")
                                          else "en-cours")
        else:
            entete["X-Soustitres"] = "ok"
        with open(cache, "w", encoding="utf-8") as f:
            f.write(vtt)
    return Response(vtt, mimetype="text/vtt", headers=entete)


# --------------------------------------------------------------- téléchargement
_downloads = {}  # vid -> {"etat", "pct", "titre", "qualite"}
_dl_lock = threading.Lock()

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
    cmd = ytdlp_cmd() + ["--no-warnings", "--newline", "--windows-filenames",
                         "--force-overwrites", "--socket-timeout", "20", "--retries", "3",
                         "--fragment-retries", "10",
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


def _tuer(proc):
    """Tue yt-dlp ET ses enfants (ffmpeg…), pour ne jamais rester bloqué."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=20)
        else:
            proc.kill()
    except Exception:                                         # noqa: BLE001
        try:
            proc.kill()
        except Exception:                                     # noqa: BLE001
            pass


def _lire_flux(proc, file_attente):
    """Lit la sortie d'yt-dlp dans un fil séparé.

    Indispensable : si un processus enfant (ffmpeg, script) garde le tuyau
    ouvert, une lecture directe resterait bloquée à vie et l'état afficherait
    « en cours » pour toujours.
    """
    try:
        for ligne in proc.stdout:
            file_attente.put(ligne)
    except Exception:                                         # noqa: BLE001
        pass
    finally:
        file_attente.put(None)


def _download_worker(vid, qualite, titre):
    _downloads[vid] = {"etat": "en cours", "pct": 0, "titre": titre,
                       "qualite": qualite, "maj": time.time()}
    if not ytdlp_cmd():
        _downloads[vid].update(etat="erreur", raison=MSG_PAS_INSTALLE)
        return
    derniere_erreur = ""
    for tentative in range(2):  # 2e essai après nettoyage si échec
        _nettoyer_fragments(vid)
        try:
            proc = subprocess.Popen(
                _cmd_download(vid, qualite),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except Exception as e:                                # noqa: BLE001
            _downloads[vid].update(etat="erreur", raison="yt-dlp n'a pas pu démarrer : " + str(e)[:150])
            return
        file_attente = queue.Queue()
        threading.Thread(target=_lire_flux, args=(proc, file_attente), daemon=True).start()
        bloque = False
        while True:
            try:
                ligne = file_attente.get(timeout=2)
            except queue.Empty:
                # Aucune nouvelle depuis TIMEOUT_STALL_DL : on arrête au lieu
                # de laisser « en cours » pour toujours.
                if time.time() - _downloads[vid].get("maj", time.time()) > TIMEOUT_STALL_DL:
                    _downloads[vid]["raison"] = "Téléchargement bloqué (plus de réponse de YouTube)."
                    _tuer(proc)
                    bloque = True
                    break
                continue
            if ligne is None:
                break
            _downloads[vid]["maj"] = time.time()
            m = re.search(r"\[download\]\s+([\d.]+)%", ligne)
            if m:
                _downloads[vid]["pct"] = float(m.group(1))
            elif "ERROR" in ligne:
                derniere_erreur = ligne.strip()
        try:
            proc.wait(timeout=15)
        except Exception:                                     # noqa: BLE001
            _tuer(proc)
        if not bloque and proc.returncode == 0:
            _nettoyer_fragments(vid)
            _downloads[vid].update(etat="terminé", pct=100)
            return
        if bloque or _downloads[vid].get("raison"):
            break
    _nettoyer_fragments(vid)
    _downloads[vid].update(
        etat="erreur",
        raison=_downloads[vid].get("raison") or _expliquer_erreur_ytdlp(derniere_erreur))


@app.route("/api/telecharger/<vid>", methods=["POST"])
def telecharger(vid):
    _verifier_id(vid)
    if not ytdlp_cmd():
        raise ErreurApi(MSG_PAS_INSTALLE, 503, "yt-dlp-absent")
    with _dl_lock:
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
        return jsonify({"ok": False, "erreur": "Fichier introuvable."}), 400
    try:
        os.remove(path)
    except OSError as e:
        return jsonify({"ok": False, "erreur": "Suppression impossible : " + str(e)[:120]}), 500
    base = os.path.splitext(path)[0]
    for srt in glob.glob(glob.escape(base) + "*.srt"):
        try:
            os.remove(srt)
        except OSError:
            pass
    return jsonify({"ok": True})


@app.route("/api/fichier/<path:nom>")
def fichier(nom):
    return send_from_directory(DOWNLOADS, nom)


@app.route("/api/soustitres-locaux/<vid>")
def soustitres_locaux(vid):
    """Sous-titres .srt d'une vidéo téléchargée, convertis en VTT (100% hors ligne)."""
    _verifier_id(vid)
    for lang in ("fr", "en"):
        for p in glob.glob(os.path.join(DOWNLOADS, f"*[[]{vid}[]].{lang}.srt")):
            with open(p, encoding="utf-8", errors="replace") as f:
                srt = f.read()
            vtt = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt)
            vtt = re.sub(r"^\d+\s*$", "", vtt, flags=re.M)
            return Response(vtt, mimetype="text/vtt")
    return Response("WEBVTT\n", mimetype="text/vtt", headers={"X-Soustitres": "indisponibles"})


@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


if __name__ == "__main__":
    host = os.environ.get("DJESSTUBE_HOST", "127.0.0.1")
    port = int(os.environ.get("DJESSTUBE_PORT", "8765"))
    print(f"DjessTube {VERSION} -> http://localhost:{port}")
    # Attention : ne rien faire ici qui puisse attendre yt-dlp, sinon le site
    # met plusieurs secondes à s'ouvrir dans le navigateur.
    if ytdlp_cmd():
        print("yt-dlp : OK")
        threading.Thread(target=preparer_traduction, daemon=True).start()
    else:
        print("!" * 60)
        print("ATTENTION : yt-dlp est introuvable.")
        print("Tape ceci dans une invite de commandes, puis relance DjessTube :")
        print("    pip install -U yt-dlp")
        print("!" * 60)
    app.run(host=host, port=port, debug=False, threaded=True)
