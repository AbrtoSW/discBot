import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from collections import deque
import webserver  # Assuming this keeps the bot alive
from dotenv import load_dotenv

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command('help')

voice_clients = {}
music_queues = {}
now_playing = {}
playlist_info = {}
loop_modes = {}
bot_config = {}

load_dotenv()

TOKEN = os.environ.get('DISCORD_TOKEN')
COOKIE_FILE = '/data/youtube_cookies.txt'  # Persistent path if using a disk on Render

# Generate browser-like headers
def get_browser_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.youtube.com/',
    }

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    if not os.path.exists('/data'):  # Ensure data directory exists if disk is mounted
        os.makedirs('/data', exist_ok=True)
    if not os.path.exists(COOKIE_FILE) or os.path.getsize(COOKIE_FILE) == 0:
        print("No cookies file found. Upload one with !uploadcookie or set a PO Token with !potoken.")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!help"))

async def get_audio_source(url, retries=3):
    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }
    for attempt in range(retries):
        try:
            return await discord.FFmpegOpusAudio.from_probe(url, **ffmpeg_options)
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt == retries - 1:
                return discord.FFmpegPCMAudio(url, **ffmpeg_options)
            await asyncio.sleep(1)

async def extract_info(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': False,
        'default_search': 'auto',
        'extract_flat': 'in_playlist',
        'nocheckcertificate': True,
        'ignoreerrors': True,
        'logtostderr': False,
        'no_warnings': True,
    }
    if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0:
        ydl_opts['cookiefile'] = COOKIE_FILE
    if 'po_token' in bot_config:
        ydl_opts['extractor_args'] = {'youtube': {'po_token': bot_config['po_token']}}
    ydl_opts['http_headers'] = get_browser_headers()

    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and len(info['entries']) > 1:
                return {
                    'is_playlist': True,
                    'entries': [{
                        'url': entry.get('url') or f"https://www.youtube.com/watch?v={entry['id']}",
                        'title': entry.get('title', 'Unknown Title'),
                        'thumbnail': entry.get('thumbnail'),
                        'duration': entry.get('duration')
                    } for entry in info['entries'] if entry.get('id')],
                    'playlist_title': info.get('title', 'Unknown Playlist')
                }
            else:
                if 'entries' in info and info['entries']:
                    info = info['entries'][0]
                return {
                    'is_playlist': False,
                    'url': info.get('url', ''),
                    'title': info.get('title', 'Unknown Title'),
                    'thumbnail': info.get('thumbnail'),
                    'duration': info.get('duration')
                }
    except Exception as e:
        print(f"Error extracting info: {str(e)}")
        return {
            'is_playlist': False,
            'url': '',
            'title': f"Error: {str(e)}",
            'thumbnail': None,
            'duration': 0
        }

async def play_next(guild_id):
    voice_client = voice_clients.get(guild_id)
    if not voice_client:
        return

    loop_mode = loop_modes.get(guild_id, 'off')
    if loop_mode == 'song' and guild_id in now_playing:
        current_song = now_playing[guild_id]
        music_queues[guild_id].appendleft(current_song)
    elif loop_mode == 'queue' and guild_id in now_playing:
        current_song = now_playing[guild_id]
        music_queues[guild_id].append(current_song)

    if guild_id not in music_queues or not music_queues[guild_id]:
        if guild_id in playlist_info:
            playlist = playlist_info[guild_id]
            if 'current_index' in playlist and playlist['current_index'] + 1 < len(playlist['entries']):
                playlist['current_index'] += 1
                next_song = playlist['entries'][playlist['current_index']]
                if guild_id not in music_queues:
                    music_queues[guild_id] = deque()
                music_queues[guild_id].append(next_song)
            else:
                now_playing.pop(guild_id, None)
                playlist_info.pop(guild_id, None)
                return
        else:
            now_playing.pop(guild_id, None)
            return

    song = music_queues[guild_id].popleft()
    now_playing[guild_id] = song

    try:
        if 'direct_url' not in song:
            if "youtube.com" in song['url'] or "youtu.be" in song['url']:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'quiet': True,
                    'noplaylist': True,
                    'nocheckcertificate': True,
                    'ignoreerrors': True,
                }
                if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0:
                    ydl_opts['cookiefile'] = COOKIE_FILE
                if 'po_token' in bot_config:
                    ydl_opts['extractor_args'] = {'youtube': {'po_token': bot_config['po_token']}}
                ydl_opts['http_headers'] = get_browser_headers()

                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    song_info = ydl.extract_info(song['url'], download=False)
                    song['url'] = song_info.get('url', song['url'])
                    song['direct_url'] = True

        if not voice_client.is_connected():
            print(f"Voice client disconnected for guild {guild_id}, attempting to reconnect")
            if hasattr(voice_client, 'channel') and voice_client.channel:
                try:
                    await voice_client.channel.connect()
                except Exception as e:
                    print(f"Failed to reconnect: {e}")
                    return

        source = await get_audio_source(song['url'])

        def after_playback(error):
            if error:
                print(f"Playback error: {error}")
            asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

        voice_client.play(source, after=after_playback)

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
    except Exception as e:
        print(f"Error playing song: {e}")
        asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

async def play_music(ctx, url, queue_if_playing=False):
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
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)

        voice_clients[ctx.guild.id] = voice_client
        voice_client.text_channel = ctx.channel

        if ctx.guild.id not in music_queues:
            music_queues[ctx.guild.id] = deque()

        info = await extract_info(url)
        if not info['url']:
            await ctx.send(f"Failed to extract information: {info['title']}")
            if "confirm you're not a bot" in info['title'] or "Sign in" in info['title']:
                await ctx.send(
                    "YouTube requires authentication. Upload a cookies file with `!uploadcookie` or set a PO Token with `!potoken`."
                )
            return

        if info['is_playlist']:
            playlist_entries = info['entries']
            if not playlist_entries:
                await ctx.send("Couldn't find any valid videos in this playlist.")
                return
            playlist_info[ctx.guild.id] = {
                'entries': playlist_entries,
                'title': info['playlist_title'],
                'current_index': 0,
                'total_songs': len(playlist_entries)
            }
            first_song = playlist_entries[0]
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
        print(f"Error details: {str(e)}")

@bot.command(name='uploadcookie')
async def upload_cookie(ctx):
    """Upload a cookies.txt file manually."""
    if not ctx.message.attachments:
        await ctx.send("Please attach a `cookies.txt` file. Export it from your browser with 'Get cookies.txt LOCALLY'.")
        return
    attachment = ctx.message.attachments[0]
    if not attachment.filename.endswith('.txt'):
        await ctx.send("Please upload a `.txt` file (e.g., `cookies.txt`).")
        return
    await attachment.save(COOKIE_FILE)
    await ctx.send("✅ Cookies file uploaded successfully! Try your command again.")

@bot.command(name='potoken')
async def set_po_token(ctx, token: str):
    """Set a YouTube PO Token for downloads."""
    bot_config['po_token'] = token
    await ctx.send("✅ PO Token set successfully! Try your command again.")

@bot.command(name='queue', aliases=['q'])
async def queue(ctx):
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
        playlist = playlist_info[ctx.guild.id]
        embed.add_field(
            name="Playlist",
            value=f"**{playlist['title']}** ({playlist.get('total_songs', len(playlist['entries']))} songs)",
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

@bot.command(name='remove')
async def remove(ctx, index: int):
    if ctx.guild.id not in music_queues or not music_queues[ctx.guild.id]:
        await ctx.send("The queue is empty.")
        return
    queue_list = music_queues[ctx.guild.id]
    if 1 <= index <= len(queue_list):
        removed = queue_list.pop(index - 1)
        await ctx.send(f"Removed: **{removed['title']}**")
    else:
        await ctx.send("Invalid index.")

@bot.command(name='loop')
async def loop(ctx, mode: str = None):
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
    if search.startswith("http"):
        url = search
    else:
        url = f"ytsearch:{search}"
    await play_music(ctx, url)

@bot.command(name='add')
async def add(ctx, *, search: str):
    if search.startswith("http"):
        url = search
    else:
        url = f"ytsearch:{search}"
    await play_music(ctx, url, queue_if_playing=True)

@bot.command(name='pause')
async def pause(ctx):
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
    if ctx.guild.id in voice_clients:
        voice_client = voice_clients[ctx.guild.id]
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()
        if ctx.guild.id in playlist_info:
            del playlist_info[ctx.guild.id]
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        try:
            await voice_client.disconnect()
        except Exception as e:
            print(f"Error disconnecting: {e}")
        del voice_clients[ctx.guild.id]
        now_playing.pop(ctx.guild.id, None)
        await ctx.send("Stopped playback, cleared queue, and disconnected.")
    else:
        await ctx.send("Not connected to a voice channel.")

@bot.command(name='skip', aliases=['next'])
async def skip(ctx):
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

@bot.command(name='commands', aliases=['help', 'cmds'])
async def commands_list(ctx):
    embed = discord.Embed(
        title="Music Bot Commands",
        description="Here are all the available commands:",
        color=discord.Color.blue()
    )
    commands_list = [
        ("!play [song]", "Plays a song immediately"),
        ("!add [song]", "Adds a song to the queue"),
        ("!pause", "Pauses the current song"),
        ("!resume", "Resumes the paused song"),
        ("!stop", "Stops playing and clears the queue"),
        ("!skip or !next", "Skips to the next song"),
        ("!queue or !q", "Shows the current queue"),
        ("!remove [index]", "Removes a song from the queue"),
        ("!clear", "Clears the music queue"),
        ("!loop", "Toggles loop modes: song → queue → off"),
        ("!loop [mode]", "Sets loop mode: 'song', 'queue', 'off'"),
        ("!np", "Shows the currently playing song"),
        ("!uploadcookie", "Uploads a YouTube cookies file"),
        ("!potoken [token]", "Sets a YouTube PO Token"),
        ("!commands", "Shows this help message")
    ]
    for command, description in commands_list:
        embed.add_field(name=command, value=description, inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        print(f"Command error: {error}")
        await ctx.send("An error occurred. Try again or check the logs.")
    raise error

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id and after.channel is None:
        guild_id = before.channel.guild.id
        if guild_id in voice_clients:
            print(f"Bot disconnected from voice in guild {guild_id}, cleaning up")
            if guild_id in music_queues:
                music_queues[guild_id].clear()
            now_playing.pop(guild_id, None)
            playlist_info.pop(guild_id, None)
            loop_modes.pop(guild_id, None)
            voice_clients.pop(guild_id, None)

if __name__ == "__main__":
    if not TOKEN:
        print("Error: No DISCORD_TOKEN found in environment variables")
        sys.exit(1)
    webserver.keep_alive()
    bot.run(TOKEN)