import os
import re
import json
import asyncio
import aiohttp
import yt_dlp
from typing import Union, Tuple, List, Dict, Optional
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch
from maythusharmusic.utils.database import is_on_off
from maythusharmusic.utils.formatters import time_to_seconds

# Configuration
YOUTUBE_API_KEY = "AIzaSyAb33ntOgvMqwaODx-4Z0rQswLSrdvmIgE"  # Replace with your actual API key
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"
INVIDIOUS_API_URL = "https://inv.riverside.rocks/api/v1"  # Primary fallback
PIPED_API_URL = "https://pipedapi.kavin.rocks"  # Secondary fallback
MAX_RETRIES = 3
REQUEST_TIMEOUT = 10

# Cookie management (from your original code)
def cookie_txt_file():
    folder_path = f"{os.getcwd()}/cookies"
    filename = f"{os.getcwd()}/cookies/logs.csv"
    txt_files = glob.glob(os.path.join(folder_path, '*.txt'))
    if not txt_files:
        raise FileNotFoundError("No .txt files found in the specified folder.")
    cookie_txt_file = random.choice(txt_files)
    with open(filename, 'a') as file:
        file.write(f'Choosen File : {cookie_txt_file}\n')
    return f"""cookies/{str(cookie_txt_file).split("/")[-1]}"""

class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        self.session = None

    async def initialize(self):
        """Initialize the aiohttp session"""
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))

    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # Helper Methods
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from various YouTube URL formats"""
        patterns = [
            r"(?:v=|be/|shorts/|live/)([\w-]{11})",
            r"youtube\.com/embed/([\w-]{11})",
            r"youtu\.be/([\w-]{11})"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _parse_duration(self, duration: str) -> str:
        """Convert ISO 8601 duration to MM:SS format"""
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return "0:00"
        
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2)) if match.group(2) else 0
        seconds = int(match.group(3)) if match.group(3) else 0
        
        total_minutes = hours * 60 + minutes
        return f"{total_minutes}:{seconds:02d}"

    # API Request Methods
    async def _make_request(self, url: str, params: dict = None, retries: int = MAX_RETRIES) -> Optional[dict]:
        """Generic request method with retries"""
        for attempt in range(retries):
            try:
                async with self.session.get(url, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 403 and 'quota' in (await response.text()).lower():
                        print("YouTube API quota exceeded")
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"Request attempt {attempt + 1} failed: {str(e)}")
                if attempt == retries - 1:
                    return None
                await asyncio.sleep(1 + attempt)  # Exponential backoff
        return None

    async def _youtube_api_search(self, query: str) -> Optional[dict]:
        """Search using YouTube Data API"""
        params = {
            "part": "snippet",
            "q": query,
            "key": YOUTUBE_API_KEY,
            "maxResults": 1,
            "type": "video"
        }
        url = f"{YOUTUBE_API_URL}/search"
        return await self._make_request(url, params)

    async def _youtube_api_video_details(self, video_id: str) -> Optional[dict]:
        """Get video details using YouTube Data API"""
        params = {
            "part": "snippet,contentDetails",
            "id": video_id,
            "key": YOUTUBE_API_KEY
        }
        url = f"{YOUTUBE_API_URL}/videos"
        return await self._make_request(url, params)

    async def _invidious_api_search(self, query: str) -> Optional[dict]:
        """Search using Invidious API"""
        params = {"q": query, "type": "video"}
        url = f"{INVIDIOUS_API_URL}/search"
        return await self._make_request(url, params)

    async def _piped_api_search(self, query: str) -> Optional[dict]:
        """Search using Piped API"""
        params = {"q": query, "filter": "videos"}
        url = f"{PIPED_API_URL}/search"
        return await self._make_request(url, params)

    # Core Functionality
    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        """Check if a YouTube link exists"""
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        """Extract URL from message"""
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        
        for message in messages:
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        return text[entity.offset:entity.offset + entity.length]
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    async def search_video(self, query: str) -> Optional[dict]:
        """Search for a video with fallback mechanisms"""
        # Try YouTube API first
        result = await self._youtube_api_search(query)
        if result and result.get('items'):
            item = result['items'][0]
            video_id = item['id']['videoId']
            details = await self._youtube_api_video_details(video_id)
            if details and details.get('items'):
                content_details = details['items'][0]['contentDetails']
                snippet = details['items'][0]['snippet']
                return {
                    "title": snippet['title'],
                    "link": f"{self.base}{video_id}",
                    "vidid": video_id,
                    "duration": self._parse_duration(content_details['duration']),
                    "thumb": snippet['thumbnails']['high']['url']
                }

        # Fallback to Invidious API
        result = await self._invidious_api_search(query)
        if result and len(result) > 0:
            video = result[0]
            return {
                "title": video['title'],
                "link": f"{self.base}{video['videoId']}",
                "vidid": video['videoId'],
                "duration": video.get('lengthSeconds', '0:00'),
                "thumb": video['videoThumbnails'][0]['url']
            }

        # Fallback to Piped API
        result = await self._piped_api_search(query)
        if result and len(result) > 0:
            video = result[0]
            return {
                "title": video['title'],
                "link": f"{self.base}{video['url'].split('=')[-1]}",
                "vidid": video['url'].split('=')[-1],
                "duration": str(video.get('duration', '0:00')),
                "thumb": video['thumbnail']
            }

        # Final fallback to VideosSearch
        try:
            results = VideosSearch(query, limit=1)
            for result in (await results.next())["result"]:
                return {
                    "title": result["title"],
                    "link": result["link"],
                    "vidid": result["id"],
                    "duration": result.get("duration", "0:00"),
                    "thumb": result["thumbnails"][0]["url"].split("?")[0]
                }
        except Exception as e:
            print(f"VideosSearch error: {e}")
        
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None) -> Tuple[str, str, int, str, str]:
        """Get video details (title, duration_min, duration_sec, thumbnail, vidid)"""
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        video_id = self.extract_video_id(link)
        if video_id:
            # Try to get details from API first
            video_info = await self.search_video(video_id)
            if video_info:
                duration_sec = int(time_to_seconds(video_info["duration"]))
                return (
                    video_info["title"],
                    video_info["duration"],
                    duration_sec,
                    video_info["thumb"],
                    video_info["vidid"]
                )

        # Fallback to VideosSearch
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result.get("duration", "0:00")
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            duration_sec = int(time_to_seconds(duration_min))
            return title, duration_min, duration_sec, thumbnail, vidid

        return "Unknown", "0:00", 0, "", ""

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        """Get video title"""
        details = await self.details(link, videoid)
        return details[0]

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        """Get video duration (MM:SS)"""
        details = await self.details(link, videoid)
        return details[1]

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        """Get video thumbnail URL"""
        details = await self.details(link, videoid)
        return details[3]

    async def video(self, link: str, videoid: Union[bool, str] = None) -> Tuple[int, str]:
        """Get direct video URL"""
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies", cookie_txt_file(),
            "-g",
            "-f",
            "best[height<=?720][width<=?1280]",
            f"{link}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        else:
            return 0, stderr.decode()

    async def playlist(self, link: str, limit: int, user_id: int, videoid: Union[bool, str] = None) -> List[str]:
        """Get playlist items"""
        if videoid:
            link = self.listbase + link
        if "&" in link:
            link = link.split("&")[0]

        playlist = await shell_cmd(
            f"yt-dlp -i --get-id --flat-playlist --cookies {cookie_txt_file()} "
            f"--playlist-end {limit} --skip-download {link}"
        )
        try:
            result = [item for item in playlist.split("\n") if item]
        except Exception:
            result = []
        return result

    async def track(self, link: str, videoid: Union[bool, str] = None) -> Tuple[Dict[str, str], str]:
        """Get track details"""
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        video_info = await self.search_video(self.extract_video_id(link) or link)
        if video_info:
            track_details = {
                "title": video_info["title"],
                "link": video_info["link"],
                "vidid": video_info["vidid"],
                "duration_min": video_info["duration"],
                "thumb": video_info["thumb"]
            }
            return track_details, video_info["vidid"]

        # Fallback to VideosSearch
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            track_details = {
                "title": result["title"],
                "link": result["link"],
                "vidid": result["id"],
                "duration_min": result["duration"],
                "thumb": result["thumbnails"][0]["url"].split("?")[0]
            }
            return track_details, result["id"]

        return {}, ""

    async def formats(self, link: str, videoid: Union[bool, str] = None) -> Tuple[List[Dict[str, Union[str, int]]], str]:
        """Get available video formats"""
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        ytdl_opts = {"quiet": True, "cookiefile": cookie_txt_file()}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        
        try:
            with ydl:
                formats_available = []
                r = ydl.extract_info(link, download=False)
                for format in r["formats"]:
                    try:
                        if not "dash" in str(format.get("format", "")).lower():
                            formats_available.append({
                                "format": format.get("format", ""),
                                "filesize": format.get("filesize", 0),
                                "format_id": format.get("format_id", ""),
                                "ext": format.get("ext", ""),
                                "format_note": format.get("format_note", ""),
                                "yturl": link,
                            })
                    except Exception:
                        continue
                return formats_available, link
        except Exception as e:
            print(f"Error getting formats: {e}")
            return [], link

    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: Union[bool, str] = None,
    ) -> Tuple[str, str, str, str]:
        """Get slider details"""
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        a = VideosSearch(link, limit=10)
        result = (await a.next()).get("result", [])
        if len(result) > query_type:
            item = result[query_type]
            return (
                item["title"],
                item.get("duration", "0:00"),
                item["thumbnails"][0]["url"].split("?")[0],
                item["id"]
            )
        return ("", "0:00", "", "")

    async def download(
        self,
        link: str,
        mystic,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> Union[str, Tuple[str, bool]]:
        """Download video or audio"""
        if videoid:
            link = self.base + link
        loop = asyncio.get_running_loop()

        def audio_dl():
            ydl_optssx = {
                "format": "bestaudio/best",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "cookiefile": cookie_txt_file(),
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "cookiefile": cookie_txt_file(),
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def song_video_dl():
            formats = f"{format_id}+140"
            fpath = f"downloads/{title}"
            ydl_optssx = {
                "format": formats,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookie_txt_file(),
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        def song_audio_dl():
            fpath = f"downloads/{title}.%(ext)s"
            ydl_optssx = {
                "format": format_id,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookie_txt_file(),
                "prefer_ffmpeg": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        if songvideo:
            await loop.run_in_executor(None, song_video_dl)
            return f"downloads/{title}.mp4"
        elif songaudio:
            await loop.run_in_executor(None, song_audio_dl)
            return f"downloads/{title}.mp3"
        elif video:
            if await is_on_off(1):
                downloaded_file = await loop.run_in_executor(None, video_dl)
                return downloaded_file, True
            else:
                proc = await asyncio.create_subprocess_exec(
                    "yt-dlp",
                    "--cookies", cookie_txt_file(),
                    "-g",
                    "-f",
                    "best[height<=?720][width<=?1280]",
                    f"{link}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if stdout:
                    return stdout.decode().split("\n")[0], False
                else:
                    downloaded_file = await loop.run_in_executor(None, video_dl)
                    return downloaded_file, True
        else:
            downloaded_file = await loop.run_in_executor(None, audio_dl)
            return downloaded_file, True

async def shell_cmd(cmd: str) -> str:
    """Execute shell command"""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, errorz = await proc.communicate()
    if errorz:
        if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower():
            return out.decode("utf-8")
        else:
            return errorz.decode("utf-8")
    return out.decode("utf-8")

async def check_file_size(link: str) -> Optional[int]:
    """Check file size of a YouTube video"""
    async def get_format_info(link: str) -> Optional[dict]:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies", cookie_txt_file(),
            "-J",
            link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f'Error:\n{stderr.decode()}')
            return None
        return json.loads(stdout.decode())

    def parse_size(formats: List[dict]) -> int:
        return sum(f.get('filesize', 0) for f in formats)

    info = await get_format_info(link)
    if info is None:
        return None
    
    formats = info.get('formats', [])
    if not formats:
        print("No formats found.")
        return None
    
    return parse_size(formats)
