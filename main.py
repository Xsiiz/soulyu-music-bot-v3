# main.py
import discord
from discord.ext import commands, tasks
import asyncio
import yt_dlp
import collections
import os
from dotenv import load_dotenv # สำหรับ .env (ถ้ามี)

# --- การตั้งค่าพื้นฐาน ---
# โหลดตัวแปรสภาพแวดล้อม (ถ้าคุณใช้ไฟล์ .env สำหรับ TOKEN)
load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") # ตั้งชื่อตัวแปรให้สอดคล้องกับไฟล์ .env ของคุณ หรือใส่ Token โดยตรง

if not BOT_TOKEN:
    print("ข้อผิดพลาด: ไม่พบ DISCORD_BOT_TOKEN ในตัวแปรสภาพแวดล้อม")
    print("โปรดตั้งค่า DISCORD_BOT_TOKEN หรือใส่ Token โดยตรงในโค้ด")
    exit()

# Intents ที่จำเป็นสำหรับบอท
intents = discord.Intents.default()
intents.message_content = True # ถ้าจะใช้คำสั่งแบบ prefix (เช่น !play) แต่เราจะเน้น slash commands
intents.voice_states = True    # จำเป็นสำหรับติดตามสถานะ voice channel

bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"), intents=intents) # prefix แทบไม่ได้ใช้เมื่อมี slash commands

# --- ตัวเลือกสำหรับ YDL และ FFMPEG ---
YDL_OPTIONS_BASE = {
    'format': 'bestaudio/best', # เลือกเสียงคุณภาพดีที่สุด
    'extract_flat': 'in_playlist', # ดึงข้อมูลเบื้องต้นของเพลงใน playlist อย่างรวดเร็ว
    'noplaylist': False,        # อนุญาตการดึงข้อมูลจาก playlist
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch', # ค้นหาบน YouTube โดยอัตโนมัติถ้าไม่ใช่ URL
    'source_address': '0.0.0.0', # ผูกกับ IP address ใดก็ได้
    'postprocessors': [{        # ประมวลผลหลังดึงข้อมูล
        'key': 'FFmpegExtractAudio', # สกัดเฉพาะเสียง
        'preferredcodec': 'opus',    # codec ที่แนะนำสำหรับ Discord
    }],
    # 'verbose': True, # เปิดเพื่อ debug yt-dlp
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', # พยายามเชื่อมต่อใหม่หากสตรีมหลุด
    'options': '-vn -filter:a "loudnorm=I=-16:LRA=11:tp=-1.5"', # -vn คือไม่เอาวิดีโอ, loudnorm สำหรับปรับระดับเสียงให้เท่ากัน
}

# --- คลาสสำหรับจัดการเพลง (Music Cog) ---
class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ข้อมูลต่อ guild (เซิร์ฟเวอร์)
        self.voice_clients = {}  # {guild_id: discord.VoiceClient}
        self.song_queues = {}    # {guild_id: collections.deque}
        self.current_songs = {}  # {guild_id: dict} (ข้อมูลเพลงที่กำลังเล่น)
        self.is_playing = {}     # {guild_id: bool}
        self.idle_timers = {}    # {guild_id: asyncio.Task} (สำหรับ auto-disconnect)
        self.loop = bot.loop     # asyncio event loop

    def _get_queue(self, guild_id: int) -> collections.deque:
        """ดึงคิวเพลงสำหรับ guild หรือสร้างใหม่ถ้ายังไม่มี"""
        if guild_id not in self.song_queues:
            self.song_queues[guild_id] = collections.deque(maxlen=50) # จำกัดคิวสูงสุด 50 เพลง
        return self.song_queues[guild_id]

    async def _ensure_voice_client(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        """ตรวจสอบหรือสร้าง voice client สำหรับ guild ของ interaction"""
        guild_id = interaction.guild_id
        if guild_id not in self.voice_clients or not self.voice_clients[guild_id].is_connected():
            if interaction.user.voice and interaction.user.voice.channel:
                channel = interaction.user.voice.channel
                try:
                    self.voice_clients[guild_id] = await channel.connect()
                except asyncio.TimeoutError:
                    await interaction.followup.send("ไม่สามารถเชื่อมต่อกับช่องเสียงได้: หมดเวลา", ephemeral=True)
                    return None
                except discord.ClientException:
                    await interaction.followup.send("ไม่สามารถเชื่อมต่อกับช่องเสียงได้: อาจกำลังเชื่อมต่ออยู่แล้ว หรือมีปัญหาอื่น", ephemeral=True)
                    return None
            else:
                await interaction.followup.send("คุณต้องอยู่ในช่องเสียงก่อน ถึงจะใช้คำสั่งนี้ได้", ephemeral=True)
                return None
        
        elif interaction.user.voice and interaction.user.voice.channel and \
             self.voice_clients[guild_id].channel != interaction.user.voice.channel:
            try:
                await self.voice_clients[guild_id].move_to(interaction.user.voice.channel)
                await interaction.followup.send(f"ย้ายไปยังช่อง: {interaction.user.voice.channel.name}", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"ไม่สามารถย้ายไปยังช่องเสียงของคุณได้: {e}", ephemeral=True)
        
        return self.voice_clients.get(guild_id)

    async def _fetch_song_data(self, query_or_url: str, requester: discord.User) -> list[dict] | None:
        """
        ดึงข้อมูลเพลง/เพลย์ลิสต์จาก yt-dlp (ทำงานใน thread แยก)
        คืนค่าเป็น list ของ dicts ที่มีข้อมูลเพลง หรือ None ถ้ามีข้อผิดพลาด
        """
        ydl_opts_custom = YDL_OPTIONS_BASE.copy()
        is_url = query_or_url.startswith('http://') or query_or_url.startswith('https://')

        search_term = query_or_url
        if not is_url:
            search_term = f"ytsearch:{query_or_url}" 
            ydl_opts_custom['extract_flat'] = False
            ydl_opts_custom['noplaylist'] = True

        def extract():
            try:
                with yt_dlp.YoutubeDL(ydl_opts_custom) as ydl:
                    info = ydl.extract_info(search_term, download=False)
                return info
            except yt_dlp.utils.DownloadError as e:
                print(f"เกิดข้อผิดพลาด yt-dlp ขณะดึงข้อมูล '{search_term}': {e}")
                return None
            except Exception as e:
                print(f"เกิดข้อผิดพลาดทั่วไปขณะดึงข้อมูล '{search_term}': {e}")
                return None

        data = await self.loop.run_in_executor(None, extract)

        if not data:
            return None

        songs_to_queue = []
        if 'entries' in data: 
            for entry in data.get('entries', []):
                if entry: 
                    songs_to_queue.append({
                        'title': entry.get('title', 'เพลงไม่ทราบชื่อ'),
                        'webpage_url': entry.get('webpage_url', entry.get('url')),
                        'duration': entry.get('duration'),
                        'thumbnail': entry.get('thumbnail'),
                        'requester': requester.mention,
                        'is_partial': True 
                    })
        else: 
            songs_to_queue.append({
                'title': data.get('title', 'เพลงไม่ทราบชื่อ'),
                'webpage_url': data.get('webpage_url'),
                'duration': data.get('duration'),
                'thumbnail': data.get('thumbnail'),
                'stream_url': data.get('url') if data.get('is_live') else None,
                'requester': requester.mention,
                'is_partial': not (data.get('url') and not data.get('is_live'))
            })
        return songs_to_queue

    async def _get_streamable_url(self, song_data: dict) -> str | None:
        """
        ดึง URL ที่สามารถสตรีมได้สำหรับเพลง (ถ้าจำเป็น)
        """
        if song_data.get('stream_url') and not song_data.get('is_partial'):
            return song_data['stream_url']

        ydl_opts_single = YDL_OPTIONS_BASE.copy()
        ydl_opts_single['extract_flat'] = False 
        ydl_opts_single['noplaylist'] = True   

        def extract_single():
            try:
                with yt_dlp.YoutubeDL(ydl_opts_single) as ydl:
                    info = ydl.extract_info(song_data['webpage_url'], download=False)
                    return info.get('url') 
            except Exception as e:
                print(f"เกิดข้อผิดพลาดขณะดึง stream URL สำหรับ '{song_data.get('title')}': {e}")
                return None
        
        stream_url = await self.loop.run_in_executor(None, extract_single)
        return stream_url

    async def _play_next_song(self, interaction: discord.Interaction):
        """เล่นเพลงถัดไปในคิว"""
        guild_id = interaction.guild_id
        queue = self._get_queue(guild_id)
        voice_client = self.voice_clients.get(guild_id)

        if not voice_client or not voice_client.is_connected():
            self.is_playing[guild_id] = False
            self.current_songs[guild_id] = None
            return

        if self.is_playing.get(guild_id, False) and not voice_client.is_playing() and not voice_client.is_paused():
            pass 

        if not queue:
            self.is_playing[guild_id] = False
            self.current_songs[guild_id] = None
            # Ensure interaction.channel is a TextChannel before sending
            if isinstance(interaction.channel, discord.TextChannel):
                 await interaction.channel.send("คิวเพลงหมดแล้ว")
            self._start_idle_timer(guild_id, interaction.channel) 
            return

        self._cancel_idle_timer(guild_id) 

        song_to_play = queue.popleft()
        self.current_songs[guild_id] = song_to_play

        stream_url = await self._get_streamable_url(song_to_play)
        if not stream_url:
            if isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.send(f"ไม่สามารถดึง URL สตรีมสำหรับเพลง '{song_to_play['title']}' ได้ กำลังข้าม...")
            self.current_songs[guild_id] = None
            self.loop.create_task(self._play_next_song(interaction))
            return

        try:
            audio_source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
            voice_client.play(audio_source, after=lambda e: self._song_finished(guild_id, interaction, e))
            self.is_playing[guild_id] = True
            
            embed = discord.Embed(title="🎧 กำลังเล่นเพลง", color=discord.Color.blue())
            embed.add_field(name="ชื่อเพลง", value=f"[{song_to_play.get('title', 'N/A')}]({song_to_play.get('webpage_url', '#')})", inline=False)
            if song_to_play.get('duration'):
                embed.add_field(name="ความยาว", value=self._format_duration(song_to_play['duration']), inline=True)
            embed.add_field(name="ขอโดย", value=song_to_play.get('requester', 'N/A'), inline=True)
            if song_to_play.get('thumbnail'):
                embed.set_thumbnail(url=song_to_play['thumbnail'])
            
            if isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.send(embed=embed)

        except Exception as e:
            if isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.send(f"เกิดข้อผิดพลาดขณะพยายามเล่นเพลง '{song_to_play.get('title', 'N/A')}': {e}")
            print(f"Playback error for '{song_to_play.get('title', 'N/A')}': {e}")
            self.is_playing[guild_id] = False
            self.current_songs[guild_id] = None
            self.loop.create_task(self._play_next_song(interaction)) 

    def _song_finished(self, guild_id: int, interaction: discord.Interaction, error=None):
        """Callback เมื่อเพลงเล่นจบ"""
        if error:
            print(f"เกิดข้อผิดพลาดระหว่างเล่นเพลงใน Guild {guild_id}: {error}")

        vc = self.voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            self.is_playing[guild_id] = False
            self.current_songs[guild_id] = None
            return

        self.loop.create_task(self._play_next_song(interaction))

    def _format_duration(self, duration_seconds: int) -> str:
        """แปลงวินาทีเป็นรูปแบบ HH:MM:SS หรือ MM:SS"""
        if not isinstance(duration_seconds, (int, float)):
            return "ไม่ทราบ"
        seconds = int(duration_seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        remaining_seconds = seconds % 60
        if hours > 0:
            return f"{hours:02}:{minutes:02}:{remaining_seconds:02}"
        else:
            return f"{minutes:02}:{remaining_seconds:02}"

    def _start_idle_timer(self, guild_id: int, text_channel_like): # text_channel_like can be Interaction.channel
        """เริ่มนับเวลาถอยหลังเพื่อตัดการเชื่อมต่อเมื่อไม่ได้ใช้งาน"""
        self._cancel_idle_timer(guild_id) 

        async def disconnect_after_idle():
            await asyncio.sleep(60) 
            voice_client = self.voice_clients.get(guild_id)
            if voice_client and voice_client.is_connected() and not self.is_playing.get(guild_id, False):
                queue = self._get_queue(guild_id)
                if not queue and not self.is_playing.get(guild_id, False) : 
                    await voice_client.disconnect()
                    self.voice_clients.pop(guild_id, None)
                    self.is_playing.pop(guild_id, None)
                    self.current_songs.pop(guild_id, None)
                    # Try to send to the original interaction's channel if possible,
                    # otherwise fall back to a known text channel.
                    target_channel = text_channel_like
                    if not isinstance(target_channel, discord.TextChannel): # If it was an interaction.channel that's not TextChannel
                        guild = self.bot.get_guild(guild_id)
                        if guild and guild.text_channels:
                             target_channel = guild.text_channels[0] # Fallback
                        else:
                            target_channel = None # Cannot send message

                    if target_channel and isinstance(target_channel, discord.abc.Messageable):
                        try:
                            await target_channel.send("ออกจากช่องเสียงเนื่องจากไม่มีการใช้งานเป็นเวลา 1 นาที")
                        except discord.Forbidden:
                            print(f"Bot lacks permission to send messages in {target_channel.name} (guild {guild_id})")
                        except Exception as e:
                            print(f"Error sending auto-disconnect message: {e}")
            self.idle_timers.pop(guild_id, None)

        voice_client = self.voice_clients.get(guild_id)
        if voice_client and voice_client.is_connected():
            is_bot_alone = len(voice_client.channel.members) <= 1 
            queue = self._get_queue(guild_id)
            if (not queue and not self.is_playing.get(guild_id, False)) or is_bot_alone:
                self.idle_timers[guild_id] = self.loop.create_task(disconnect_after_idle())

    def _cancel_idle_timer(self, guild_id: int):
        """ยกเลิก timer การตัดการเชื่อมต่ออัตโนมัติ"""
        if guild_id in self.idle_timers:
            self.idle_timers[guild_id].cancel()
            self.idle_timers.pop(guild_id, None)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """จัดการเมื่อสถานะ voice ของสมาชิกมีการเปลี่ยนแปลง"""
        if member.bot and member.id != self.bot.user.id: 
            return

        guild_id = member.guild.id
        voice_client = self.voice_clients.get(guild_id)

        if not voice_client or not voice_client.is_connected():
            return 

        if member.id == self.bot.user.id and after.channel is None:
            self.is_playing.pop(guild_id, None)
            self.current_songs.pop(guild_id, None)
            self.voice_clients.pop(guild_id, None)
            self._cancel_idle_timer(guild_id)
            print(f"บอทถูกตัดการเชื่อมต่อจากช่องเสียงใน Guild {guild_id}")
            return

        text_channel_for_notification = voice_client.channel.guild.text_channels[0] if voice_client.channel.guild.text_channels else None


        if before.channel == voice_client.channel and after.channel != voice_client.channel:
            if len(voice_client.channel.members) <= 1: 
                queue = self._get_queue(guild_id)
                if not queue and not self.is_playing.get(guild_id, False):
                    if text_channel_for_notification:
                        self._start_idle_timer(guild_id, text_channel_for_notification)
                elif len(voice_client.channel.members) <= 1: 
                     if text_channel_for_notification:
                        self._start_idle_timer(guild_id, text_channel_for_notification)


        if after.channel == voice_client.channel and member.id != self.bot.user.id:
            if guild_id in self.idle_timers:
                if len(voice_client.channel.members) > 1:
                    self._cancel_idle_timer(guild_id)

    # --- Slash Commands ---
    @discord.app_commands.command(name="join", description="ให้บอทเข้าร่วมช่องเสียงของคุณ")
    async def join(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) 
        voice_client = await self._ensure_voice_client(interaction)
        if voice_client:
            await interaction.followup.send(f"เข้าร่วมช่อง: {voice_client.channel.name} เรียบร้อยแล้ว", ephemeral=True)
            self._cancel_idle_timer(interaction.guild_id) 

    @discord.app_commands.command(name="leave", description="ให้บอทออกจากช่องเสียง")
    async def leave(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id
        voice_client = self.voice_clients.get(guild_id)

        if voice_client and voice_client.is_connected():
            queue = self._get_queue(guild_id)
            queue.clear() 
            self.is_playing[guild_id] = False
            self.current_songs[guild_id] = None
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
            
            await voice_client.disconnect()
            self.voice_clients.pop(guild_id, None)
            self._cancel_idle_timer(guild_id)
            await interaction.followup.send("ออกจากช่องเสียงแล้ว และล้างคิวเพลงทั้งหมด")
        else:
            await interaction.followup.send("บอทไม่ได้อยู่ในช่องเสียงใดๆ", ephemeral=True)

    @discord.app_commands.command(name="play", description="เล่นเพลงจากชื่อ, URL (Youtube, Mix, Playlist)")
    @discord.app_commands.describe(query="ชื่อเพลง, URL ของ Youtube, Mix หรือ Playlist")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer() 

        voice_client = await self._ensure_voice_client(interaction)
        if not voice_client:
            return

        guild_id = interaction.guild_id
        self._cancel_idle_timer(guild_id) 

        songs_data = await self._fetch_song_data(query, interaction.user)

        if not songs_data:
            await interaction.followup.send(f"ไม่พบผลลัพธ์สำหรับ '{query}' หรือเกิดข้อผิดพลาดในการดึงข้อมูล")
            if not self._get_queue(guild_id) and not self.is_playing.get(guild_id, False):
                self._start_idle_timer(guild_id, interaction.channel)
            return

        queue = self._get_queue(guild_id)
        added_count = 0
        is_playlist = len(songs_data) > 1

        for song_info in songs_data:
            if len(queue) < queue.maxlen:
                queue.append(song_info)
                added_count += 1
            else:
                await interaction.followup.send(f"คิวเต็มแล้ว (สูงสุด {queue.maxlen} เพลง). ไม่สามารถเพิ่ม '{song_info.get('title', 'N/A')}' ได้", ephemeral=True)
                break 

        if added_count == 0 and not songs_data: 
             await interaction.followup.send(f"ไม่พบเพลงสำหรับ '{query}'")
             return
        elif added_count == 0 and songs_data: 
            pass


        if is_playlist and added_count > 0:
            await interaction.followup.send(f"เพิ่ม {added_count} เพลงจากเพลย์ลิสต์เข้าคิวเรียบร้อยแล้ว")
        elif not is_playlist and added_count > 0:
             await interaction.followup.send(f"เพิ่มเพลง '{songs_data[0].get('title', 'N/A')}' เข้าคิวแล้ว")


        if not self.is_playing.get(guild_id, False) and queue:
            self.loop.create_task(self._play_next_song(interaction))
        elif not queue and not self.is_playing.get(guild_id, False):
            self._start_idle_timer(guild_id, interaction.channel)


    @discord.app_commands.command(name="skip", description="ข้ามเพลงปัจจุบัน")
    async def skip(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id
        voice_client = self.voice_clients.get(guild_id)

        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop() 
            await interaction.followup.send("ข้ามเพลงปัจจุบันแล้ว")
        else:
            await interaction.followup.send("ไม่มีเพลงกำลังเล่นอยู่", ephemeral=True)

    @discord.app_commands.command(name="queue", description="แสดงคิวเพลงปัจจุบัน")
    async def show_queue(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        queue = self._get_queue(guild_id)
        current_song = self.current_songs.get(guild_id)

        if not queue and not current_song:
            await interaction.followup.send("คิวเพลงว่างเปล่า และไม่มีเพลงกำลังเล่น", ephemeral=True)
            return

        embed = discord.Embed(title="🎶 คิวเพลงปัจจุบัน 🎶", color=discord.Color.purple())
        
        if current_song:
            embed.add_field(
                name="▶️ กำลังเล่น",
                value=f"[{current_song.get('title', 'N/A')}]({current_song.get('webpage_url', '#')}) (ขอโดย: {current_song.get('requester', 'N/A')})",
                inline=False
            )
        
        # --- MODIFIED SECTION FOR QUEUE DISPLAY ---
        if queue:
            queue_list = list(queue) 
            output_lines = []
            current_char_count = 0
            MAX_CHARS = 1024 
            REMAINDER_MSG_BUFFER = 35 # Approx chars for "... และอีก XX เพลง"

            num_songs_actually_displayed = 0

            for i, song_data in enumerate(queue_list):
                line = f"{i + 1}. [{song_data.get('title', 'เพลงไม่ทราบชื่อ')}]({song_data.get('webpage_url', '#')}) (ขอโดย: {song_data.get('requester', 'ไม่ระบุ')})\n"

                if len(line) > MAX_CHARS: # Highly unlikely for a single line, but a safeguard
                    line = line[:MAX_CHARS - 4] + "...\n" 

                # Check if adding this line would exceed the allowed space before the "remainder" message
                # or if it's the very first line and it's too long even for the full MAX_CHARS
                if (current_char_count + len(line) > MAX_CHARS - REMAINDER_MSG_BUFFER and num_songs_actually_displayed > 0) or \
                   (num_songs_actually_displayed == 0 and len(line) > MAX_CHARS): # First line itself is too long
                    if num_songs_actually_displayed == 0 and len(line) > MAX_CHARS: # First song is too long for the whole field
                        output_lines.append(line[:MAX_CHARS - 4] + "...\n")
                        current_char_count += len(output_lines[0])
                        num_songs_actually_displayed = 1
                    break 
                
                # If it's the first song and it fits (even if it uses up most of the space)
                if num_songs_actually_displayed == 0 and current_char_count + len(line) > MAX_CHARS:
                     output_lines.append(line[:MAX_CHARS -4] + "...\n")
                     current_char_count += len(output_lines[0])
                     num_songs_actually_displayed = 1
                     break


                output_lines.append(line)
                current_char_count += len(line)
                num_songs_actually_displayed += 1
            
            final_queue_text = "".join(output_lines)
            remaining_in_queue_count = len(queue_list) - num_songs_actually_displayed

            if remaining_in_queue_count > 0:
                remainder_msg_str = f"\n... และอีก {remaining_in_queue_count} เพลง"
                if current_char_count + len(remainder_msg_str) <= MAX_CHARS:
                    final_queue_text += remainder_msg_str
                else: # Not enough space for full remainder, just add ellipsis if possible
                    if MAX_CHARS - current_char_count > 3: # Check if there's space for "..."
                         final_queue_text = final_queue_text[:MAX_CHARS - 3] + "..."
                    else: # No space at all, final_queue_text might already be at MAX_CHARS or truncated
                         final_queue_text = final_queue_text[:MAX_CHARS-3] + "..."


            if not final_queue_text and queue_list: 
                final_queue_text = "คิวเพลงยาวเกินไปที่จะแสดงผลทั้งหมด"
            elif not queue_list and not current_song: # No queue and no current song was handled earlier
                final_queue_text = "ไม่มีเพลงในคิว"
            elif not queue_list and current_song: # Current song exists, but queue is empty
                 final_queue_text = "ไม่มีเพลงในคิว"


            # Final safety truncate
            if len(final_queue_text) > MAX_CHARS:
                final_queue_text = final_queue_text[:MAX_CHARS - 3] + "..."
            
            field_title = f"📜 รายการถัดไป ({num_songs_actually_displayed}/{len(queue_list)})" if queue_list else "📜 รายการถัดไป"
            embed.add_field(name=field_title, value=final_queue_text if final_queue_text else "ไม่มีเพลงในคิว", inline=False)
        elif current_song : # Current song exists, but queue is empty
            embed.add_field(name="📜 รายการถัดไป", value="ไม่มีเพลงในคิว", inline=False)
        # If no current_song and no queue, it's handled by the initial check.
        # --- END OF MODIFIED SECTION ---

        embed.set_footer(text=f"จำนวนเพลงในคิว: {len(queue)}/{queue.maxlen}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="remove", description="ลบเพลงออกจากคิวตามหมายเลข")
    @discord.app_commands.describe(index="หมายเลขเพลงในคิวที่จะลบ (ดูจาก /queue)")
    async def remove(self, interaction: discord.Interaction, index: int):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        queue = self._get_queue(guild_id)

        if not queue:
            await interaction.followup.send("คิวเพลงว่างเปล่า", ephemeral=True)
            return

        if 1 <= index <= len(queue):
            try:
                temp_list = list(queue)
                removed_song = temp_list.pop(index - 1)
                queue.clear()
                queue.extend(temp_list) 
                await interaction.followup.send(f"ลบเพลง '{removed_song.get('title', 'N/A')}' ออกจากคิวแล้ว", ephemeral=True)
            except IndexError:
                 await interaction.followup.send("หมายเลขเพลงไม่ถูกต้อง", ephemeral=True)
        else:
            await interaction.followup.send(f"โปรดระบุหมายเลขระหว่าง 1 ถึง {len(queue)}", ephemeral=True)

    @discord.app_commands.command(name="clear", description="ล้างคิวเพลงทั้งหมด")
    async def clear(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id
        queue = self._get_queue(guild_id)
        
        if queue:
            queue.clear()
            await interaction.followup.send("ล้างคิวเพลงทั้งหมดแล้ว")
        else:
            await interaction.followup.send("คิวเพลงว่างเปล่าอยู่แล้ว", ephemeral=True)

    @discord.app_commands.command(name="pause", description="หยุดเล่นเพลงชั่วคราว")
    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id
        voice_client = self.voice_clients.get(guild_id)

        if voice_client and voice_client.is_playing():
            voice_client.pause()
            self._cancel_idle_timer(guild_id) 
            self._start_idle_timer(guild_id, interaction.channel) 
            await interaction.followup.send("หยุดเล่นเพลงชั่วคราว ⏸️")
        else:
            await interaction.followup.send("ไม่มีเพลงกำลังเล่น หรือเพลงถูกหยุดไปแล้ว", ephemeral=True)

    @discord.app_commands.command(name="resume", description="เล่นเพลงต่อจากที่หยุดไว้")
    async def resume(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id
        voice_client = self.voice_clients.get(guild_id)

        if voice_client and voice_client.is_paused():
            voice_client.resume()
            self._cancel_idle_timer(guild_id) 
            await interaction.followup.send("เล่นเพลงต่อ ▶️")
        else:
            await interaction.followup.send("ไม่มีเพลงที่หยุดไว้ หรือกำลังเล่นเพลงอยู่", ephemeral=True)
            
    @discord.app_commands.command(name="nowplaying", description="แสดงเพลงที่กำลังเล่นอยู่")
    async def nowplaying(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        current_song = self.current_songs.get(guild_id)
        voice_client = self.voice_clients.get(guild_id)

        if current_song and voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            status = "กำลังเล่น" if voice_client.is_playing() else "หยุดชั่วคราว"
            embed = discord.Embed(title=f"🎧 เพลงปัจจุบัน ({status})", color=discord.Color.green())
            embed.add_field(name="ชื่อเพลง", value=f"[{current_song.get('title', 'N/A')}]({current_song.get('webpage_url', '#')})", inline=False)
            if current_song.get('duration'):
                embed.add_field(name="ความยาว", value=self._format_duration(current_song['duration']), inline=True)
            embed.add_field(name="ขอโดย", value=current_song.get('requester', 'N/A'), inline=True)
            if current_song.get('thumbnail'):
                embed.set_thumbnail(url=current_song['thumbnail'])
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("ไม่มีเพลงกำลังเล่นอยู่", ephemeral=True)

# --- Event Listener ของ Bot ---
@bot.event
async def on_ready():
    print(f'{bot.user.name} ออนไลน์แล้ว!')
    print(f'ID: {bot.user.id}')
    print(f'เชื่อมต่อกับ {len(bot.guilds)} เซิร์ฟเวอร์')
    try:
        await bot.add_cog(MusicCog(bot))
        synced = await bot.tree.sync()
        print(f"ซิงค์ {len(synced)} คำสั่งเรียบร้อยแล้ว")

    except Exception as e:
        print(f"เกิดข้อผิดพลาดระหว่างการ sync commands: {e}")

# --- การรันบอท ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("กรุณาตั้งค่า BOT_TOKEN ก่อนรันบอท")
    else:
        try:
            bot.run(BOT_TOKEN)
        except discord.LoginFailure:
            print("Login ไม่สำเร็จ: Token ไม่ถูกต้องหรือมีปัญหาในการเชื่อมต่อ")
        except Exception as e:
            print(f"เกิดข้อผิดพลาดขณะรันบอท: {e}")

