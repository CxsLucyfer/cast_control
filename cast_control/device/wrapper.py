from __future__ import annotations

import logging
from decimal import Decimal
from enum import StrEnum
from mimetypes import guess_type
from typing import Any, NamedTuple, Optional, Self
from urllib.parse import parse_qs, urlparse

# from pychromecast.controllers.yleareena import YleAreenaController
# from pychromecast.controllers.homeassistant import HomeAssistantController
# from pychromecast.controllers.plex import PlexApiController
# from pychromecast.controllers.bbciplayer import BbcIplayerController
# from pychromecast.controllers.bbcsounds import BbcSoundsController
# from pychromecast.controllers.bubbleupnp import BubbleUPNPController
from mpris_server import (
  BEGINNING, DEFAULT_RATE, DbusObj, MetadataObj, Microseconds, Paths, PlayState, Rate,
  ValidMetadata, Volume, get_track_id
)
from pychromecast.controllers.dashcast import DashCastController
from pychromecast.controllers.media import BaseController, MediaController, MediaStatus
from pychromecast.controllers.plex import PlexController
from pychromecast.controllers.receiver import CastStatus
from pychromecast.controllers.supla import SuplaController
from pychromecast.controllers.youtube import YouTubeController

from .. import TITLE
from ..app.state import create_desktop_file, ensure_user_dirs_exist
from ..base import DEFAULT_DISC_NO, DEFAULT_ICON, DEFAULT_THUMB, Device, \
  LIGHT_THUMB, MediaType, NAME, NO_DELTA, NO_DESKTOP_FILE, \
  NO_DURATION, US_IN_SEC, singleton
from ..types import Final, Protocol


RESOLUTION: Final[int] = 1
MAX_TITLES: Final[int] = 3

NO_ARTIST: Final[str] = ''
NO_SUFFIX: Final[str] = ''

SKIP_FIRST: Final[slice] = slice(1, None)
VIDEO_QS: Final[str] = 'v'


class YoutubeUrl(StrEnum):
  long: Self = 'youtube.com'
  short: Self = 'youtu.be'

  watch: Self = f'https://{long}/watch?v='

  @classmethod
  def get_url(cls: type[Self], content_id: str | None) -> str | None:
    if not content_id:
      return None

    return f"{cls.watch}{content_id}"

  @classmethod
  def is_youtube(cls: type[Self], uri: str | None) -> bool:
    if not uri:
      return False

    return is_youtube(uri)


class CachedIcon(NamedTuple):
  url: str
  app_id: str
  title: str


class Titles(NamedTuple):
  title: Optional[str] = None
  artist: Optional[str] = None
  album: Optional[str] = None


class Controllers(NamedTuple):
  yt: Optional[YouTubeController] = None
  dash: Optional[DashCastController] = None
  plex: Optional[PlexController] = None
  supla: Optional[SuplaController] = None
  # bbc_ip: BbcIplayerController = None
  # bbc_sound: BbcSoundsController = None
  # bubble: BubbleUPNPController = None
  # yle: YleAreenaController = None
  # plex_api: PlexApiController = None
  # ha: HomeAssistantController = None


class Wrapper(Protocol):
  dev: Device
  ctls: Controllers
  cached_icon: Optional[CachedIcon] = None
  light_icon: bool = DEFAULT_ICON

  def __getattr__(self, name: str) -> Any:
    return getattr(self.dev, name)

  @property
  def name(self) -> str:
    return self.dev.name or NAME

  @property
  def cast_status(self) -> Optional[CastStatus]:
    pass

  @property
  def media_status(self) -> Optional[MediaStatus]:
    pass

  @property
  def media_controller(self) -> MediaController:
    pass

  @property
  def titles(self) -> Titles:
    pass

  def on_new_status(self, *args, **kwargs):
    '''Callback for event listener'''
    pass


class StatusMixin(Wrapper):
  @property
  def cast_status(self) -> Optional[CastStatus]:
    return self.dev.status or None

  @property
  def media_status(self) -> Optional[MediaStatus]:
    return self.media_controller.status or None

  @property
  def media_controller(self) -> MediaController:
    return self.dev.media_controller


class ControllersMixin(Wrapper):
  def __init__(self):
    self._setup_controllers()
    super().__init__()

  def _setup_controllers(self):
    self.ctls = Controllers(
      YouTubeController(),
      DashCastController(),
      PlexController(),
      SuplaController(),
      # BbcIplayerController(),
      # BbcSoundsController(),
      # BubbleUPNPController(),
      # YleAreenaController(),
      # PlexApiController(),
      # HomeAssistantController(),
    )

    for ctl in self.ctls:
      if ctl:
        self._register(ctl)

  def _register(self, controller: BaseController):
    self.dev.register_handler(controller)

  def _launch_youtube(self):
    self.ctls.yt.launch()

  def _play_youtube(self, video_id: str):
    yt = self.ctls.yt

    if not yt.is_active:
      self._launch_youtube()

    yt.play_video(video_id)

  def _is_youtube_vid(self, content_id: str | None) -> bool:
    if not content_id or not self.ctls.yt.is_active:
      return False

    return not content_id.startswith('http')

  def _get_url(self) -> Optional[str]:
    content_id: str | None = None

    if self.media_status:
      content_id = self.media_status.content_id

    if self._is_youtube_vid(content_id):
      return YoutubeUrl.get_url(content_id)

    return content_id

  def open_uri(self, uri: str):
    if video_id := get_content_id(uri):
      self._play_youtube(video_id)
      return

    mimetype, _ = guess_type(uri)
    self.media_controller.play_media(uri, mimetype)

  def add_track(
    self,
    uri: str,
    after_track: DbusObj,
    set_as_current: bool
  ):
    yt = self.ctls.yt

    if video_id := get_content_id(uri):
      yt.add_to_queue(video_id)

    if video_id and set_as_current:
      yt.play_video(video_id)

    elif set_as_current:
      self.open_uri(uri)


class TitlesMixin(Wrapper):
  @property
  def titles(self) -> Titles:
    titles: list[str] = list()

    if title := self.media_controller.title:
      titles.append(title)

    if (status := self.media_status) and (series_title := status.series_title):
      titles.append(series_title)

    if subtitle := self.get_subtitle():
      titles.append(subtitle)

    if status:
      if artist := status.artist:
        titles.append(artist)

      if album := status.album_name:
        titles.append(album)

    if app_name := self.dev.app_display_name:
      titles.append(app_name)

    if not titles:
      titles.append(TITLE)

    titles = titles[:MAX_TITLES]

    return Titles(*titles)

  def get_subtitle(self) -> Optional[str]:
    if not self.media_status:
      return None

    if not (metadata := self.media_status.media_metadata):
      return None

    if subtitle := metadata.get('subtitle'):
      return subtitle

    return None


class TimeMixin(Wrapper):
  _longest_duration: float = NO_DURATION

  def __init__(self):
    self._longest_duration = NO_DURATION
    super().__init__()

  @property
  def current_time(self) -> Optional[float]:
    status = self.media_status

    if not status:
      return None

    return status.adjusted_current_time or status.current_time

  def get_duration(self) -> Microseconds:
    duration: Optional[int] = None

    if self.media_status:
      duration = self.media_status.duration

    if duration is not None:
      return duration * US_IN_SEC

    longest: int = self._longest_duration
    current = self.get_current_position()

    if longest and longest > current:
      return longest

    elif current:
      self._longest_duration = current
      return current

    return NO_DURATION

  def get_current_position(self) -> Microseconds:
    position_secs = self.current_time

    if not position_secs:
      return BEGINNING

    position_us = position_secs * US_IN_SEC
    return round(position_us)

  def on_new_status(self, *args, **kwargs):
    # super().on_new_status(*args, **kwargs)
    if not self.has_current_time():
      self._longest_duration = None

  def has_current_time(self) -> bool:
    current_time = self.current_time

    if current_time is None:
      return False

    current_time = round(current_time, RESOLUTION)

    return current_time > BEGINNING

  def seek(self, time: Microseconds):
    seconds = int(round(time / US_IN_SEC))
    self.media_controller.seek(seconds)

  def get_rate(self) -> Rate:
    if not self.media_status:
      return DEFAULT_RATE

    if rate := self.media_status.playback_rate:
      return rate

    return DEFAULT_RATE

  def set_rate(self, val: Rate):
    pass


class IconsMixin(Wrapper):
  def _set_cached_icon(self, url: Optional[str] = None):
    if not url:
      self.cached_icon = None
      return

    app_id = self.dev.app_id
    title, *_ = self.titles
    self.cached_icon = CachedIcon(url, app_id, title)

  def _can_use_cache(self) -> bool:
    if not (icon := self.cached_icon) or not icon.url:
      return False

    app_id = self.dev.app_id
    title, *_ = self.titles

    return icon.app_id == app_id and icon.title == title

  def _get_icon_from_device(self) -> Optional[str]:
    url: str | None

    if images := self.media_status.images:
      first, *_ = images
      url, *_ = first

      self._set_cached_icon(url)
      return url

    if self.cast_status and (url := self.cast_status.icon_url):
      self._set_cached_icon(url)
      return url

    if not self._can_use_cache():
      return None

    return self.cached_icon.url

  @ensure_user_dirs_exist
  def _get_default_icon(self) -> str:
    if self.light_icon:
      return str(LIGHT_THUMB)

    return str(DEFAULT_THUMB)

  def get_art_url(self, track: Optional[int] = None) -> str:
    if icon := self._get_icon_from_device():
      return icon

    return self._get_default_icon()

  @singleton
  def get_desktop_entry(self) -> Paths:
    try:
      path = create_desktop_file(self.light_icon)

    except Exception as e:
      logging.exception(e)
      logging.error("Couldn't load desktop file.")
      return NO_DESKTOP_FILE

    return path

  def set_icon(self, lighter: bool = False):
    self.light_icon: bool = lighter


class MetadataMixin(Wrapper):
  def metadata(self) -> ValidMetadata:
    title, artist, album = self.titles

    dbus_name: DbusObj = get_track_id(title)
    artists: list[str] = [artist] if artist else []
    comments: list[str] = []
    track_no: Optional[int] = None

    if self.media_status:
      track_no = self.media_status.track

    return MetadataObj(
      track_id=dbus_name,
      length=self.get_duration(),
      art_url=self.get_art_url(),
      url=self._get_url(),
      title=title,
      artists=artists,
      album=album,
      album_artists=artists,
      disc_number=DEFAULT_DISC_NO,
      track_number=track_no,
      comments=comments,
    )


class PlaybackMixin(Wrapper):
  def get_playstate(self) -> PlayState:
    if self.media_controller.is_playing:
      return PlayState.PLAYING

    elif self.media_controller.is_paused:
      return PlayState.PAUSED

    return PlayState.STOPPED

  def is_repeating(self) -> bool:
    return False

  def is_playlist(self) -> bool:
    return self.can_go_next() or self.can_go_previous()

  def get_shuffle(self) -> bool:
    return False

  def set_shuffle(self, val: bool):
    pass

  def play_next(self):
    self.media_controller.queue_next()

  def play_prev(self):
    self.media_controller.queue_prev()

  def quit(self):
    self.dev.quit_app()

  def next(self):
    self.play_next()

  def previous(self):
    self.play_prev()

  def pause(self):
    self.media_controller.pause()

  def resume(self):
    self.play()

  def stop(self):
    self.media_controller.stop()

  def play(self):
    self.media_controller.play()

  def set_repeating(self, val: bool):
    pass

  def set_loop_status(self, val: str):
    pass


class VolumeMixin(Wrapper):
  #def __init__(self):
    #super().__init__()

  def get_volume(self) -> Optional[Volume]:
    if not self.cast_status:
      return None

    return Decimal(self.cast_status.volume_level)

  def set_volume(self, val: Volume):
    val = Decimal(val)
    curr = self.get_volume()

    if curr is None:
      return

    delta: float = float(val - curr)

    # can't adjust vol by 0
    if delta > NO_DELTA:  # vol up
      self.dev.volume_up(delta)

    elif delta < NO_DELTA:
      self.dev.volume_down(abs(delta))

  def is_mute(self) -> Optional[bool]:
    if self.cast_status:
      return self.cast_status.volume_muted

    return False

  def set_mute(self, val: bool):
    self.dev.set_volume_muted(val)


class AbilitiesMixin(Wrapper):
  def can_quit(self) -> bool:
    return True

  def can_play(self) -> bool:
    state = self.get_playstate()

    return state is not PlayState.STOPPED

  def can_control(self) -> bool:
    return True
    # return self.can_play() or self.can_pause() \
    #   or self.can_play_next() or self.can_play_prev() \
    #   or self.can_seek()

  def can_edit_track(self) -> bool:
    return False

  def can_play_next(self) -> bool:
    if status := self.media_status:
      return status.supports_queue_next

    return False

  def can_play_prev(self) -> bool:
    if status := self.media_status:
      return status.supports_queue_prev

    return False

  def can_pause(self) -> bool:
    if status := self.media_status:
      return status.supports_pause

    return False

  def can_seek(self) -> bool:
    if status := self.media_status:
      return status.supports_seek

    return False


class DeviceWrapper(
  StatusMixin,
  TitlesMixin,
  ControllersMixin,
  TimeMixin,
  IconsMixin,
  MetadataMixin,
  PlaybackMixin,
  VolumeMixin,
  AbilitiesMixin,
):
  '''Wraps implementation details for device API'''

  def __init__(self, dev: Device):
    self.dev = dev
    super().__init__()

  def __repr__(self) -> str:
    cls = type(self)
    cls_name = cls.__name__

    return f'<{cls_name} for {self.dev}>'


def get_media_type(
  dev: DeviceWrapper
) -> Optional[MediaType]:
  status = dev.media_status

  if not status:
    return None

  if status.media_is_movie:
    return MediaType.MOVIE

  elif status.media_is_tvshow:
    return MediaType.TVSHOW

  elif status.media_is_photo:
    return MediaType.PHOTO

  elif status.media_is_musictrack:
    return MediaType.MUSICTRACK

  elif status.media_is_generic:
    return MediaType.GENERIC

  return None


def is_youtube(uri: str) -> bool:
  uri = uri.casefold()
  parsed = urlparse(uri)

  return any(url in parsed.netloc for url in YoutubeUrl)


def get_content_id(uri: str) -> Optional[str]:
  if not YoutubeUrl.is_youtube(uri):
    return None

  content_id: str | None = None
  parsed = urlparse(uri)

  match parsed.netloc:
    case YoutubeUrl.long:
      qs = parse_qs(parsed.query)
      [content_id] = qs[VIDEO_QS]

    case YoutubeUrl.short:
      content_id = parsed.path[SKIP_FIRST]

  return content_id
