import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from collections import deque
from dotenv import load_dotenv

# Set up Discord intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command('help')

# Global dictionaries for managing music
voice_clients = {}
music_queues = {}
now_playing = {}
playlist_info = {}
loop_modes = {}

# Load environment variables
load_dotenv()
TOKEN = os.environ.get('DISCORD_TOKEN')
PO_TOKEN = os.environ.get('PO_TOKEN')  # Optional: Add PO_TOKEN to .env if needed

# Cookies file setup
COOKIES_FILE = 'cookies.txt'
if os.path.exists(COOKIES_FILE):
    print(f"Found {COOKIES_FILE} - cookies will be used for authentication.")
else:
    print(f"Warning: {COOKIES_FILE} not found in {os.getcwd()}. Restricted videos may fail.")
if PO_TOKEN:
    print(f"PO Token found in environment variables - will be used for authentication.")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!help"))

async def extract_info(url):
    """Extract information from a YouTube URL or search query."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': False,
        'default_search': 'auto',
        'extract_flat': 'in_playlist',
        'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
    }
    if PO_TOKEN:
        ydl_opts['po_token'] = PO_TOKEN

    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            print(f"Extracting info for: {url} with cookies from {COOKIES_FILE if os.path.exists(COOKIES_FILE) else 'None'}"
                  f"{' and PO Token' if PO_TOKEN else ''}")
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and len(info['entries']) > 1:
                return {
                    'is_playlist': True,
                    'entries': [{
                        'url': entry.get('url') or f"https://www.youtube.com/watch?v={entry['id']}",
                        'title': entry['title'],
                        'thumbnail': entry.get('thumbnail'),
                        'duration': entry.get('duration')
                    } for entry in info['entries']],
                    'playlist_title': info.get('title', 'Unknown Playlist')
                }
            else:
                if 'entries' in info:
                    info = info['entries'][0]
                return {
                    'is_playlist': False,
                    'url': info['url'],
                    'title': info['title'],
                    'thumbnail': info.get('thumbnail'),
                    'duration': info.get('duration')
                }
    except youtube_dl.utils.DownloadError as e:
        error_msg = str(e)
        print(f"Extraction failed: {error_msg}")
        if "Sign in to confirm" in error_msg or "cookies" in error_msg:
            return {
                "error": (
                    f"Authentication required: {error_msg}. "
                    f"Ensure a valid {COOKIES_FILE} is in {os.getcwd()} and contains up-to-date YouTube cookies. "
                    "You may also need a PO Token (see https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide). "
                    "For cookies, see https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
                )
            }
        raise e

async def play_next(guild_id):
    """Play the next song in the queue or repeat based on loop mode."""
    voice_client = voice_clients.get(guild_id)
    if not voice_client:
        print(f"No voice client found for guild {guild_id}")
        return

    loop_mode = loop_modes.get(guild_id, 'off')
    if loop_mode in ['song', 'queue'] and guild_id in now_playing:
        current_song = now_playing[guild_id]
        if loop_mode == 'song':
            music_queues[guild_id].appendleft(current_song)
        elif loop_mode == 'queue':
            music_queues[guild_id].append(current_song)

    if guild_id not in music_queues or not music_queues[guild_id]:
        if guild_id in playlist_info:
            playlist = playlist_info[guild_id]
            if playlist['current_index'] + 1 < len(playlist['entries']):
                playlist['current_index'] += 1
                next_song = playlist['entries'][playlist['current_index']]
                music_queues[guild_id].append(next_song)
            else:
                now_playing.pop(guild_id, None)
                playlist_info.pop(guild_id, None)
                print(f"Playlist finished for guild {guild_id}")
                return
        else:
            now_playing.pop(guild_id, None)
            print(f"Queue empty for guild {guild_id}")
            return

    song = music_queues[guild_id].popleft()
    now_playing[guild_id] = song

    if 'direct_url' not in song:
        if "youtube.com" in song['url'] or "youtu.be" in song['url']:
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'noplaylist': True,
                'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
            }
            if PO_TOKEN:
                ydl_opts['po_token'] = PO_TOKEN
            try:
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    print(f"Fetching direct URL for: {song['url']}")
                    song_info = ydl.extract_info(song['url'], download=False)
                song['url'] = song_info['url']
                song['direct_url'] = True
            except youtube_dl.utils.DownloadError as e:
                error_msg = str(e)
                print(f"Playback fetch failed: {error_msg}")
                if "Sign in to confirm" in error_msg or "cookies" in error_msg:
                    if hasattr(voice_client, 'text_channel'):
                        await voice_client.text_channel.send(
                            f"Error: {error_msg}. Ensure a valid {COOKIES_FILE} is in {os.getcwd()} and up-to-date. "
                            "A PO Token may also be required (see https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide). "
                            "For cookies, seeUnity https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
                        )
                return

    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }

    try:
        source = await discord.FFmpegOpusAudio.from_probe(song['url'], **ffmpeg_options)
    except Exception as e:
        print(f"FFmpegOpusAudio failed: {e}")
        source = discord.FFmpegPCMAudio(song['url'], **ffmpeg_options)

    def after_playback(error):
        if error:
            print(f"Playback error for guild {guild_id}: {error}")
        asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

    voice_client.play(source, after=after_playback)
    print(f"Playing: {song['title']} in guild {guild_id}")

    if hasattr(voice_client, 'text_channel'):
        embed = discord.Embed(
            title="Now Playing",
            description=f"**{song['title']}**",
            color=discord.Color.blue()
        )
        if song.get('thumbnail'):
            embed.set_thumbnail(url=song['thumbnail'])
        if song.get('duration'):
            minutes, seconds = divmod(song['duration'], 60)
            embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}")
        await voice_client.text_channel.send(embed=embed)

async def play_music(ctx, url, queue_if_playing=False):
    """Play music from a URL or search query."""
    try:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
        else:
            channel = None
            for vc in ctx.guild.voice_channels:
                if any(not member.bot for member in vc.members):
                    channel = vc
                    break
            if channel is None:
                await ctx.send("No active voice channel found with a non-bot user. Please join a voice channel first.")
                return

        voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
        if voice_client is None:
            voice_client = await channel.connect()
            print(f"Connected to voice channel {channel.name} in guild {ctx.guild.id}")
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)
            print(f"Moved to voice channel {channel.name} in guild {ctx.guild.id}")

        voice_clients[ctx.guild.id] = voice_client
        voice_client.text_channel = ctx.channel

        if ctx.guild.id not in music_queues:
            music_queues[ctx.guild.id] = deque()

        info = await extract_info(url)
        if "error" in info:
            await ctx.send(info["error"])
            return

        if info['is_playlist']:
            playlist_info[ctx.guild.id] = {
                'entries': info['entries'],
                'title': info['playlist_title'],
                'current_index': 0,
                'total_songs': len(info['entries'])
            }
            first_song = info['entries'][0]
            music_queues[ctx.guild.id].append(first_song)
            await ctx.send(f"Added playlist: **{info['playlist_title']}** (Starting with **{first_song['title']}**)")
            if not voice_client.is_playing() and not queue_if_playing:
                await play_next(ctx.guild.id)
        else:
            if queue_if_playing and voice_client.is_playing():
                music_queues[ctx.guild.id].append(info)
                await ctx.send(f"Added to queue: **{info['title']}**")
            else:
                if voice_client.is_playing():
                    voice_client.stop()
                music_queues[ctx.guild.id].append(info)
                await play_next(ctx.guild.id)

    except Exception as e:
        await ctx.send(f"Error: {str(e)}")
        print(f"Play music error in guild {ctx.guild.id}: {str(e)}")

@bot.command(name='queue', aliases=['q'])
async def queue(ctx):
    """Shows the current music queue."""
    if ctx.guild.id not in music_queues or not music_queues[ctx.guild.id]:
        await ctx.send("The queue is empty.")
        return

    queue_list = list(music_queues[ctx.guild.id])
    embed = discord.Embed(
        title="Music Queue",
        description=f"Total songs in queue: {len(queue_list)}",
        color=discord.Color.blue()
    )

    if ctx.guild.id in playlist_info:
        embed.add_field(
            name="Playlist",
            value=f"**{playlist_info[ctx.guild.id]['title']}** "
                  f"({playlist_info[ctx.guild.id]['total_songs']} songs)",
            inline=False
        )

    if ctx.guild.id in now_playing:
        current = now_playing[ctx.guild.id]
        embed.add_field(
            name="Now Playing",
            value=f"**{current['title']}**",
            inline=False
        )

    if queue_list:
        upcoming = "\n".join([f"{i + 1}. {song['title']}" for i, song in enumerate(queue_list[:10])])
        remaining = len(queue_list) - 10 if len(queue_list) > 10 else 0
        embed.add_field(
            name="Upcoming Songs",
            value=upcoming + (f"\n\n*and {remaining} more...*" if remaining else ""),
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name='loop')
async def loop(ctx, mode: str = None):
    """Toggles loop mode: song, queue, or off."""
    guild_id = ctx.guild.id
    valid_modes = ['off', 'song', 'queue']

    if mode:
        mode = mode.lower()
        if mode not in valid_modes:
            await ctx.send("Invalid mode. Choose from: `off`, `song`, `queue`")
            return
        loop_modes[guild_id] = mode
        await ctx.send(f"Loop mode set to **{mode}**.")
    else:
        current = loop_modes.get(guild_id, 'off')
        next_mode = valid_modes[(valid_modes.index(current) + 1) % len(valid_modes)]
        loop_modes[guild_id] = next_mode
        await ctx.send(f"Loop mode toggled to **{next_mode}**.")

@bot.command(name='play')
async def play(ctx, *, search: str):
    """Plays a song from YouTube immediately."""
    print(f"Play command triggered by {ctx.author} with search: {search}")
    if search.startswith("http"):
        url = search
    else:
        url = f"ytsearch:{search}"
    await play_music(ctx, url)

@bot.command(name='add')
async def add(ctx, *, search: str):
    """Adds a song to the queue."""
    print(f"Add command triggered by {ctx.author} with search: {search}")
    if search.startswith("http"):
        url = search
    else:
        url = f"ytsearch:{search}"
    await play_music(ctx, url, queue_if_playing=True)

@bot.command(name='pause')
async def pause(ctx):
    """Pauses the currently playing song."""
    if ctx.guild.id in voice_clients:
        voice_client = voice_clients[ctx.guild.id]
        if voice_client.is_playing():
            voice_client.pause()
            await ctx.send("Playback paused.")
        else:
            await ctx.send("Nothing is playing.")
    else:
        await ctx.send("Not connected to a voice channel.")

@bot.command(name='resume')
async def resume(ctx):
    """Resumes the currently paused song."""
    if ctx.guild.id in voice_clients:
        voice_client = voice_clients[ctx.guild.id]
        if voice_client.is_paused():
            voice_client.resume()
            await ctx.send("Playback resumed.")
        else:
            await ctx.send("The audio is not paused.")
    else:
        await ctx.send("Not connected to a voice channel.")

@bot.command(name='stop')
async def stop(ctx):
    """Stops playing, clears the queue, and disconnects."""
    if ctx.guild.id in voice_clients:
        voice_client = voice_clients[ctx.guild.id]
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()
        if ctx.guild.id in playlist_info:
            del playlist_info[ctx.guild.id]
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        await voice_client.disconnect()
        del voice_clients[ctx.guild.id]
        now_playing.pop(ctx.guild.id, None)
        await ctx.send("Stopped playback, cleared queue, and disconnected.")
    else:
        await ctx.send("Not connected to a voice channel.")

@bot.command(name='skip', aliases=['next'])
async def skip(ctx):
    """Skips to the next song in the queue."""
    if ctx.guild.id in voice_clients:
        voice_client = voice_clients[ctx.guild.id]
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
            await ctx.send("Skipped to the next song.")
        else:
            await ctx.send("Nothing is playing.")
    else:
        await ctx.send("Not connected to a voice channel.")

@bot.command(name='clear')
async def clear(ctx):
    """Clears the music queue and stops current playback."""
    guild_id = ctx.guild.id
    queue_cleared = False
    if guild_id in music_queues:
        music_queues[guild_id].clear()
        queue_cleared = True
    now_playing.pop(guild_id, None)
    playlist_info.pop(guild_id, None)
    loop_modes.pop(guild_id, None)
    if guild_id in voice_clients:
        voice_client = voice_clients[guild_id]
        if voice_client.is_playing():
            voice_client.stop()
    if queue_cleared:
        await ctx.send("✅ Queue and playback have been cleared.")
    else:
        await ctx.send("ℹ️ Queue is already empty.")

@bot.command(name='np', aliases=['nowplaying'])
async def now_playing_cmd(ctx):
    """Shows the currently playing song."""
    if ctx.guild.id in now_playing:
        song = now_playing[ctx.guild.id]
        embed = discord.Embed(
            title="Now Playing",
            description=f"**{song['title']}**",
            color=discord.Color.blue()
        )
        if song.get('thumbnail'):
            embed.set_thumbnail(url=song['thumbnail'])
        if song.get('duration'):
            minutes, seconds = divmod(song['duration'], 60)
            embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}")
        await ctx.send(embed=embed)
    else:
        await ctx.send("Nothing is currently playing.")

@bot.command(name='commands', aliases=['help'])
async def commands_list(ctx):
    """Shows all available commands."""
    embed = discord.Embed(
        title="Music Bot Commands",
        description="Here are all the available commands:",
        color=discord.Color.blue()
    )
    commands_list = [
        ("!play [song/url]", "Plays a song immediately"),
        ("!add [song/url]", "Adds a song to the queue"),
        ("!pause", "Pauses the current song"),
        ("!resume", "Resumes the paused song"),
        ("!stop", "Stops playback and disconnects"),
        ("!skip or !next", "Skips to the next song"),
        ("!queue or !q", "Shows the current queue"),
        ("!clear", "Clears the queue"),
        ("!loop", "Toggles loop modes: off → song → queue"),
        ("!loop [mode]", "Sets loop mode: 'off', 'song', 'queue'"),
        ("!np", "Shows the currently playing song"),
        ("!commands or !help", "Shows this help message")
    ]
    for command, description in commands_list:
        embed.add_field(name=command, value=description, inline=False)
    await ctx.send(embed=embed)

# Ensure the help command is removed
bot.remove_command('help')

# Run the bot
if TOKEN:
    print("Starting bot...")
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Failed to start bot: {e}")
else:
    print("Error: No DISCORD_TOKEN found in environment variables")
    print("Please set your token in a .env file or environment variables")