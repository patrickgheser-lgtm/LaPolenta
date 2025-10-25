# bot_setup.py
# ----------------------------------------
# Discord Music Bot con supporto YouTube + Spotify
# ----------------------------------------

import os
import discord
from discord.ext import commands
from discord import app_commands
from collections import deque
import yt_dlp
import asyncio
import concurrent.futures
from keep_alive import keep_alive

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# ----------------------------------------
# Variabili ambiente
# ----------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not DISCORD_TOKEN:
    raise ValueError("❌ Variabile ambiente DISCORD_TOKEN non trovata.")

# ----------------------------------------
# Config Spotify API
# ----------------------------------------
sp = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    ))
    print("🎧 Spotify API collegata con successo.")
else:
    print("⚠️ Spotify non configurato (nessuna variabile ambiente trovata).")

# ----------------------------------------
# Song queues per server
# ----------------------------------------
SONG_QUEUES = {}

# ----------------------------------------
# yt_dlp Async Helper
# ----------------------------------------
async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await asyncio.wait_for(
                loop.run_in_executor(pool, lambda: _extract(query, ydl_opts)),
                timeout=15.0
            )
    except Exception as e:
        print(f"❌ yt_dlp error: {e}")
        return None

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

# ----------------------------------------
# Discord Bot Setup
# ----------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ {bot.user} è online e sincronizzato!")

# ----------------------------------------
# Funzione: estrai titoli da link Spotify
# ----------------------------------------
def get_spotify_tracks(url):
    if not sp:
        return []
    tracks = []

    try:
        if "track" in url:
            track = sp.track(url)
            tracks.append(f"{track['name']} - {track['artists'][0]['name']}")
        elif "playlist" in url:
            results = sp.playlist_items(url)
            for item in results['items']:
                t = item['track']
                tracks.append(f"{t['name']} - {t['artists'][0]['name']}")
        elif "album" in url:
            results = sp.album_tracks(url)
            for t in results['items']:
                tracks.append(f"{t['name']} - {t['artists'][0]['name']}")
    except Exception as e:
        print(f"❌ Errore Spotify: {e}")
    return tracks

# ----------------------------------------
# /play
# ----------------------------------------
@bot.tree.command(name="play", description="Riproduci un brano o una playlist da YouTube o Spotify")
@app_commands.describe(query="Titolo, link YouTube o link Spotify")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice:
        await interaction.followup.send("❌ Devi essere in un canale vocale.")
        return

    voice_channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    if vc is None:
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'source_address': '0.0.0.0',
        'extractor_retries': 3,
        'noplaylist': True,
        'default_search': 'ytsearch',
        'age_limit': 0,
        'extractor_args': {'youtube': {'player_client': ['android']}},
    }

    guild_id = str(interaction.guild_id)
    SONG_QUEUES.setdefault(guild_id, deque())

    # --- Se è un link Spotify ---
    if "open.spotify.com" in query:
        tracks = get_spotify_tracks(query)
        if not tracks:
            await interaction.followup.send("❌ Nessun brano trovato su Spotify.")
            return
        await interaction.followup.send(f"🎧 Aggiungo {len(tracks)} brani da Spotify...")
        for track in tracks:
            yt_query = f"ytsearch1:{track}"
            results = await search_ytdlp_async(yt_query, ydl_opts)
            if results and results.get("entries"):
                first = results["entries"][0]
                SONG_QUEUES[guild_id].append((first["url"], first["title"]))
        await play_next(vc, guild_id, interaction.channel)
        return

    # --- Altrimenti cerca su YouTube ---
    results = await search_ytdlp_async(f"ytsearch1:{query}", ydl_opts)
    if not results or not results.get("entries"):
        await interaction.followup.send("❌ Nessun risultato trovato.")
        return

    first = results["entries"][0]
    SONG_QUEUES[guild_id].append((first["url"], first["title"]))
    await interaction.followup.send(f"🎶 Aggiunto alla coda: **{first['title']}**")

    if not vc.is_playing():
        await play_next(vc, guild_id, interaction.channel)

# ----------------------------------------
# /skip, /pause, /resume, /stop
# ----------------------------------------
@bot.tree.command(name="skip", description="Salta il brano corrente.")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Brano saltato.")
    else:
        await interaction.response.send_message("❌ Nessun brano in riproduzione.")

@bot.tree.command(name="pause", description="Metti in pausa la riproduzione.")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ In pausa.")
    else:
        await interaction.response.send_message("❌ Nessun brano in riproduzione.")

@bot.tree.command(name="resume", description="Riprendi la riproduzione.")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Ripreso.")
    else:
        await interaction.response.send_message("❌ Nessun brano in pausa.")

@bot.tree.command(name="stop", description="Ferma la musica e disconnetti il bot.")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    guild_id = str(interaction.guild_id)
    SONG_QUEUES[guild_id].clear()
    if vc:
        await vc.disconnect()
    await interaction.response.send_message("⏹️ Fermato e disconnesso.")

# ----------------------------------------
# Funzione riproduzione
# ----------------------------------------
async def play_next(vc, guild_id, channel):
    if SONG_QUEUES[guild_id]:
        url, title = SONG_QUEUES[guild_id].popleft()
        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        try:
            source = discord.FFmpegOpusAudio(url, **ffmpeg_opts)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(vc, guild_id, channel), asyncio.get_event_loop()))
            await channel.send(f"🎵 In riproduzione: **{title}**")
        except Exception as e:
            await channel.send(f"❌ Errore nella riproduzione di {title}: {e}")
            await play_next(vc, guild_id, channel)
    else:
        await channel.send("✅ Coda terminata.")
        await vc.disconnect()

# ----------------------------------------
# Avvio server Flask + bot
# ----------------------------------------
if __name__ == "__main__":
    keep_alive()
    bot.run(DISCORD_TOKEN)
