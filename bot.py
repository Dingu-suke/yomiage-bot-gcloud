import asyncio
import io
import logging
import os
import re

import discord
from discord.ext import commands
from google.cloud import texttospeech

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yomiage")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GCP_VOICE_NAME = os.environ.get("GCP_VOICE_NAME", "ja-JP-Neural2-B")
GCP_LANGUAGE_CODE = os.environ.get("GCP_LANGUAGE_CODE", "ja-JP")
GCP_SPEAKING_RATE = float(os.environ.get("GCP_SPEAKING_RATE", "1.0"))
MAX_TEXT_LEN = 100


class TextProcessor:
    URL_RE = re.compile(r"https?://\S+")
    CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
    MENTION_RE = re.compile(r"<@!?\d+>|<#\d+>|<@&\d+>")
    UNICODE_EMOJI_RE = re.compile(
        "["
        "\U0001F300-\U0001FAFF"
        "\U00002600-\U000027BF"
        "\U0001F000-\U0001F2FF"
        "\uFE0F\u200D"
        "]+",
        flags=re.UNICODE,
    )
    LAUGH_RE = re.compile(
        r"[(（]+\s*(?:笑+|泣+|怒+|爆+|嬉+|[wW]+)\s*[)）]+"
        r"|笑+(?=[。、．！？!?\s]|$)"
    )
    SENTENCE_RE = re.compile(r"[^。．！？!?、\n]+[。．！？!?、]?")

    @classmethod
    def sanitize(cls, text: str) -> str:
        text = cls.URL_RE.sub("URL", text)
        text = cls.CUSTOM_EMOJI_RE.sub("\n", text)
        text = cls.MENTION_RE.sub("", text)
        text = cls.UNICODE_EMOJI_RE.sub("\n", text)
        text = cls.LAUGH_RE.sub("\n", text)
        text = text.strip()
        if len(text) > MAX_TEXT_LEN:
            text = text[:MAX_TEXT_LEN] + " 以下略"
        return text

    @classmethod
    def split_sentences(cls, text: str) -> list[str]:
        return [s.strip(" 　、\n") for s in cls.SENTENCE_RE.findall(text) if s.strip(" 　、\n")]


class GoogleTTSClient:
    def __init__(self, voice_name: str, language_code: str = "ja-JP", speaking_rate: float = 1.0):
        # gRPC 非同期クライアントは生成時のイベントループに紐付くため、
        # bot.run() が作るループ内（最初の合成時）で遅延生成する。
        # 認証は GOOGLE_APPLICATION_CREDENTIALS 環境変数から自動で読まれる
        self._client: texttospeech.TextToSpeechAsyncClient | None = None
        self.voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name,
        )
        self.audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.OGG_OPUS,
            speaking_rate=speaking_rate,
        )

    async def synthesize(self, text: str) -> bytes:
        if self._client is None:
            self._client = texttospeech.TextToSpeechAsyncClient()
        response = await self._client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=self.voice,
            audio_config=self.audio_config,
        )
        return response.audio_content


class GuildPlayer:
    """1 ギルドあたり 1 インスタンス。キューと文単位パイプライン再生を担う。"""

    def __init__(self, guild_id: int, synthesizer: GoogleTTSClient):
        self.guild_id = guild_id
        self.synthesizer = synthesizer
        self.queue: asyncio.Queue[tuple[str, discord.VoiceClient]] = asyncio.Queue()
        self.read_channel_id: int | None = None
        self._task: asyncio.Task | None = None

    def set_read_channel(self, channel_id: int) -> None:
        self.read_channel_id = channel_id

    def clear_read_channel(self) -> None:
        self.read_channel_id = None

    def is_target_channel(self, channel_id: int) -> bool:
        return self.read_channel_id == channel_id

    async def enqueue(self, text: str, vc: discord.VoiceClient) -> None:
        await self.queue.put((text, vc))

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            text, vc = await self.queue.get()
            if not vc.is_connected():
                continue
            sentences = TextProcessor.split_sentences(text)
            if not sentences:
                continue
            await self._play_pipelined(sentences, vc)

    async def _play_pipelined(
        self,
        sentences: list[str],
        vc: discord.VoiceClient,
    ) -> None:
        synth_task: asyncio.Task[bytes] = asyncio.create_task(
            self.synthesizer.synthesize(sentences[0])
        )

        for i in range(len(sentences)):
            try:
                audio = await synth_task
            except Exception:
                log.exception("synthesize failed")
                audio = None

            if i + 1 < len(sentences):
                next_text = sentences[i + 1]
                synth_task = asyncio.create_task(
                    self.synthesizer.synthesize(next_text)
                )

            if audio is None or not vc.is_connected():
                continue
            await self._play_audio(vc, audio)

    @staticmethod
    async def _play_audio(vc: discord.VoiceClient, audio: bytes) -> None:
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        source = discord.FFmpegPCMAudio(io.BytesIO(audio), pipe=True)
        vc.play(source, after=lambda e: loop.call_soon_threadsafe(done.set))
        await done.wait()


class PlayerManager:
    def __init__(self, synthesizer: GoogleTTSClient):
        self.synthesizer = synthesizer
        self._players: dict[int, GuildPlayer] = {}

    def get(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(guild_id, self.synthesizer)
        return self._players[guild_id]


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
synthesizer = GoogleTTSClient(
    voice_name=GCP_VOICE_NAME,
    language_code=GCP_LANGUAGE_CODE,
    speaking_rate=GCP_SPEAKING_RATE,
)
manager = PlayerManager(synthesizer)


@bot.event
async def on_ready():
    await bot.tree.sync()
    log.info("Logged in as %s", bot.user)


@bot.tree.command(name="join", description="あなたが居るボイスチャンネルに参加します")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("先にVCに参加してください", ephemeral=True)
        return
    channel = interaction.user.voice.channel
    guild = interaction.guild

    if guild.voice_client:
        await guild.voice_client.move_to(channel)
    else:
        await channel.connect()

    player = manager.get(guild.id)
    player.set_read_channel(channel.id)
    player.ensure_running()

    await interaction.response.send_message(
        f"{channel.name} に参加しました。VC内のチャットを読み上げます"
    )


@bot.tree.command(name="leave", description="ボイスチャンネルから退出します")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("VCに参加していません", ephemeral=True)
        return
    await vc.disconnect()
    manager.get(interaction.guild.id).clear_read_channel()
    await interaction.response.send_message("退出しました")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot:
        return
    vc = member.guild.voice_client
    if not vc or not vc.is_connected():
        return
    if before.channel == after.channel:  # ミュート等の状態変化は無視
        return

    bot_channel_id = vc.channel.id
    if after.channel and after.channel.id == bot_channel_id:
        verb = "参戦"
    elif before.channel and before.channel.id == bot_channel_id:
        verb = "離脱"
    else:
        return

    name = TextProcessor.sanitize(member.display_name) or "だれか"
    player = manager.get(member.guild.id)
    player.ensure_running()
    await player.enqueue(f"{name} が{verb}しました", vc)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    player = manager.get(message.guild.id)
    if not player.is_target_channel(message.channel.id):
        return
    vc = message.guild.voice_client
    if not vc or not vc.is_connected():
        return
    text = TextProcessor.sanitize(message.content)
    if not text:
        return
    await player.enqueue(text, vc)


bot.run(DISCORD_TOKEN)
