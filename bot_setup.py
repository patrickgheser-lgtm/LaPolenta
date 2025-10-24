# bot_setup.py
# ----------------------------------------
# Discord Music Bot (yt_dlp + FFmpeg)
# Compatibile con PythonAnywhere / UptimeRobot
# ----------------------------------------

import os
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from collections import deque
import asyncio
import concurrent.futures
from keep_alive import keep_alive

# ----------------------------------------
# TOKEN (dal pannello Environment su PythonAnywhere)
# ----------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ Environment variable DISCORD_TOKEN not found.")

# ----------------------------------------
# Coda musicale
# ----------------------------------------
SONG_QUEUES = {}

# ----------------------------------------
# yt_dlp — ricerca asincrona
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
# Setup bot Discord
# ----------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ {bot.user} è online e sincronizzato!")

# ----------------------------------------
# /play
# ----------------------------------------
@bot.tree.command(name="play", description="Riproduci una canzone da YouTube")
@app_commands.describe(song_query="Titolo o link YouTube")
async def play(interaction: discord.Interaction, song_query: str):
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

    ytdl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'source_address': '0.0.0.0',
        'noplaylist': True,
        'default_search': 'ytsearch',
        'age_limit': 0,
    }

    results = await search_ytdlp_async(f"ytsearch1:{song_query}", ytdl_opts)
    if not results or "entries" not in results:
        await interaction.followup.send("❌ Nessun risultato trovato.")
        return

    track = results["entries"][0]
    audio_url = track["url"]
    title = track.get("title", "Sconosciuto")

    guild_id = str(interaction.guild_id)
    SONG_QUEUES.setdefault(guild_id, deque()).append((audio_url, title))

    if vc.is_playing() or vc.is_paused():
        await interaction.followup.send(f"➕ Aggiunta alla coda: **{title}**")
    else:
        await interaction.followup.send(f"🎶 Riproduzione di: **{title}**")
        await play_next_song(vc, guild_id, interaction.channel)


# ----------------------------------------
# /skip, /pause, /resume, /stop
# ----------------------------------------
@bot.tree.command(name="skip", description="Salta la canzone attuale")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Brano saltato.")
    else:
        await interaction.response.send_message("❌ Nessuna canzone in riproduzione.")


@bot.tree.command(name="pause", description="Metti in pausa la canzone")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Pausa.")
    else:
        await interaction.response.send_message("❌ Nessuna canzone da mettere in pausa.")


@bot.tree.command(name="resume", description="Riprendi la canzone")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Ripresa.")
    else:
        await interaction.response.send_message("❌ Nessuna canzone in pausa.")


@bot.tree.command(name="stop", description="Ferma e svuota la coda")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        SONG_QUEUES[str(interaction.guild_id)] = deque()
        await vc.disconnect()
        await interaction.response.send_message("⏹️ Fermato e disconnesso.")
    else:
        await interaction.response.send_message("❌ Non sono in nessun canale vocale.")


# ----------------------------------------
# Riproduzione
# ----------------------------------------
async def play_next_song(vc, guild_id, channel):
    if SONG_QUEUES[guild_id]:
        url, title = SONG_QUEUES[guild_id].popleft()
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

        try:
            source = discord.FFmpegOpusAudio(url, **ffmpeg_options)
            vc.play(
                source,
                after=lambda e: asyncio.run_coroutine_threadsafe(
                    play_next_song(vc, guild_id, channel), asyncio.get_event_loop()
                ),
            )
            await channel.send(f"🎵 Ora in riproduzione: **{title}**")
        except Exception as e:
            await channel.send(f"❌ Errore nella riproduzione: {e}")
    else:
        await channel.send("✅ Coda terminata.")
        await vc.disconnect()


# ----------------------------------------
# Avvio bot e server
# ----------------------------------------
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
