# DjessTube

YouTube en mieux, en local : recherche sur tout YouTube, lecture 1080p sans pub,
téléchargement (1080p / 720p / MP3) et sous-titres nettoyés.

## Lancer

1. Double-clique sur **`LANCER DjessTube.bat`**
2. Le navigateur s'ouvre sur <http://localhost:8765>
3. **Garde la fenêtre noire ouverte** pendant que tu utilises le site : c'est le serveur.

Au premier lancement, le fichier `.bat` installe tout seul Flask et yt-dlp.

## Ça charge à l'infini — que faire ?

Le rond qui tourne sans fin venait d'un problème d'affichage des erreurs : quand
le serveur avait un souci, la page attendait une réponse qui n'arrivait jamais.
C'est corrigé : maintenant, un message clair s'affiche avec un bouton **🔄 Réessayer**.

Si tu vois quand même un de ces messages, voici la cause et la solution :

| Message affiché | Cause | Solution |
|---|---|---|
| « yt-dlp n'est pas installé… » | yt-dlp manque sur le PC | Dans une invite de commandes : `pip install -U yt-dlp`, puis relance le `.bat` |
| « Aucun résultat n'a pu être chargé… yt-dlp trop ancien » | YouTube a changé, yt-dlp est dépassé | `pip install -U yt-dlp` (YouTube casse les vieilles versions tous les 1-2 mois) |
| « yt-dlp ne répond pas (plus de 90 s) » | Internet lent, VPN, ou YouTube bloque | Vérifie la connexion, coupe le VPN, réessaie |
| « Serveur injoignable » | La fenêtre noire a été fermée | Relance `LANCER DjessTube.bat` |
| « Aucun flux lisible trouvé pour cette vidéo » | Vidéo privée, réservée aux abonnés ou bloquée dans ton pays | Essaie une autre vidéo |
| « Traduction indisponible : tape pip install argostranslate » | Le module de traduction n'est pas installé | `pip install argostranslate` (facultatif) |

**Le réflexe qui règle 90 % des problèmes :**

```
pip install -U yt-dlp
```

## Où vont les fichiers ?

| Dossier | Contenu |
|---|---|
| `telechargements/` | vidéos et MP3 téléchargés (lisibles sans Internet depuis l'onglet « Hors ligne ») |
| `cache_soustitres/` | sous-titres déjà récupérés (chargement instantané la 2ᵉ fois) |

Ces deux dossiers ne sont pas envoyés sur GitHub (voir `.gitignore`).

## Réglages facultatifs (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `DJESSTUBE_PORT` | `8765` | port du site |
| `DJESSTUBE_HOST` | `127.0.0.1` | interface d'écoute (`0.0.0.0` pour y accéder depuis un autre appareil du réseau) |
| `DJESSTUBE_TIMEOUT_RECHERCHE` | `90` | délai maxi pour une recherche (secondes) |
| `DJESSTUBE_TIMEOUT_VIDEO` | `90` | délai maxi pour récupérer les infos d'une vidéo |
| `DJESSTUBE_TIMEOUT_TELECHARGEMENT` | `180` | téléchargement sans réponse avant abandon |
