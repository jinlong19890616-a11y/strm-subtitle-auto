from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import queue
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.metainfo import MetaInfoPath
from app.db.models.transferhistory import TransferHistory
from app.log import logger
from app.plugins import _PluginBase

try:
    from app.core.plugin import PluginManager
except Exception:
    PluginManager = None

try:
    from app.chain.tmdb import TmdbChain
except Exception:
    TmdbChain = None

try:
    from app.schemas.types import MediaType
except Exception:
    class _NoopMediaType:
        MOVIE = "movie"
        TV = "tv"

    MediaType = _NoopMediaType

try:
    from app.core.event import eventmanager, Event as MPEvent
    from app.schemas.types import EventType
except Exception:
    class _NoopEventManager:
        @staticmethod
        def register(_event_type):
            def decorator(func):
                return func

            return decorator

    class _NoopEventType:
        TransferComplete = "transfer.complete"

    eventmanager = _NoopEventManager()
    MPEvent = Any
    EventType = _NoopEventType

from .matching.subtitle_formats import SUBTITLE_EXTS
from .online.online_subtitle import (
    OnlineSubtitleSearchService,
    build_search_keywords,
    check_online_rate_limit,
    extract_title_aliases,
)
from .config.config_schema import (
    AUTO_MULTI_SUBTITLE_MODES,
    AUTO_TRANSFER_SUBTITLE_STRATEGIES,
    AUTO_TRANSFER_SUBTITLE_STRATEGY_ALIASES,
    AVAILABLE_ONLINE_PROVIDER_IDS,
    DEFAULT_ASSRT_API_URL,
    DEFAULT_ENGINE,
    DEFAULT_ONLINE_PROVIDER_IDS,
    DEFAULT_OPENSUBTITLES_API_URL,
    DEFAULT_PROVIDER_ROOTS,
    DEFAULT_RAR_TOOL_PATH,
    MANUAL_ONLINE_PROVIDER_IDS,
    RAR_DEPENDENCY_MODES,
    build_config_form,
    host_from_url,
    normalize_auto_multi_subtitle_mode,
    normalize_auto_transfer_subtitle_strategy,
    normalize_online_site_urls,
    normalize_plugin_config,
    normalize_provider_ids,
    normalize_rar_dependency_mode,
    normalize_root_url,
    normalize_timeline_max_offset,
    normalize_timeline_min_offset,
    normalize_timeline_vad_mode,
)
from .config.config_runtime import (
    apply_runtime_config,
    build_save_config_payload,
    reset_runtime_state,
    sync_class_runtime_config,
)
from .runtime.runtime_helpers import (
    apply_tmdb_detail as runtime_apply_tmdb_detail,
    brief_ids as runtime_brief_ids,
    cache_loaded_at as runtime_cache_loaded_at,
    decode_preview_bytes as runtime_decode_preview_bytes,
    entry_filesystem_signature as runtime_entry_filesystem_signature,
    entry_matches_keyword as runtime_entry_matches_keyword,
    entry_path_is_valid as runtime_entry_path_is_valid,
    extract_episode_hint as runtime_extract_episode_hint,
    hash_text as runtime_hash_text,
    is_stream_path as runtime_is_stream_path,
    is_upload_file as runtime_is_upload_file,
    json_clone as runtime_json_clone,
    media_type_text as runtime_media_type_text,
    normalize_text as runtime_normalize_text,
    ok_response,
    poster_url as runtime_poster_url,
    safe_int as runtime_safe_int,
    timestamp_iso as runtime_timestamp_iso,
    tmdb_aliases as runtime_tmdb_aliases,
    tmdb_detail_payload as runtime_tmdb_detail_payload,
)
from .matching.subtitle_language import (
    DEFAULT_AUTO_FORMAT_PRIORITY,
    DEFAULT_AUTO_LANGUAGE_PRIORITY,
    LANGUAGE_SUFFIX_ALIASES,
    auto_language_bucket,
    auto_subtitle_sort_key,
    detect_language_profile,
    is_chinese_language_suffix,
    language_suffix_from_filename,
    normalize_auto_format_priority,
    normalize_auto_language_key,
    normalize_auto_language_priority,
    normalize_language_suffix,
)
from .integrations.autosub_bridge import AutoSubBridge, autosub_task_summary as bridge_autosub_task_summary
from .matching.subtitle_history import SubtitleHistory
from .matching.subtitle_writer import (
    SubtitleWriter,
    backup_subtitle_if_needed as writer_backup_subtitle_if_needed,
    build_destination_name as writer_build_destination_name,
    build_write_operations as writer_build_write_operations,
    subtitle_backup_path as writer_subtitle_backup_path,
    timeline_rejection_message as writer_timeline_rejection_message,
    timeline_result_blocks_auto_write as writer_timeline_result_blocks_auto_write,
)
from .timeline.timeline_fixer import TimelineFixResult, check_timeline_fixer_dependencies, fix_subtitle_timeline
from .matching.tongwen import convert_subtitle_file_to_simplified
from .catalog.target_resolver import (
    LocalMediaCatalog,
    MediaTargetResolver,
    SubtitleInventory,
)
from .timeline.timeline_tasks import timeline_task_summary
from .auto_transfer import AutoTransferService
from .runtime.service_registry import StrmSubtitleAutoServices
from .api.routes import build_api_routes
from .runtime.shell_helpers import install_shell_helpers


class StrmSubtitleAuto(_PluginBase):
    plugin_name = "STRM 中文字幕自动处理"
    plugin_desc = "等待 Symedia 生成 STRM，并在 STRM 旁边自动写入中文字幕外挂。"
    plugin_icon = "https://raw.githubusercontent.com/imac/strm-subtitle-auto/main/icons/hyrule-subtitle-master.png"
    plugin_version = "0.1.0"
    plugin_author = "ifsherlock"
    author_url = "https://github.com/ifsherlock"
    plugin_config_prefix = "strmsubtitleauto_"
    plugin_order = 48
    auth_level = 1

    _enabled = False
    _show_sidebar_nav = True
    _rar_dependency_mode = "none"
    _rar_tool_path = DEFAULT_RAR_TOOL_PATH
    _default_online_provider_ids = list(DEFAULT_ONLINE_PROVIDER_IDS)
    _available_online_provider_ids = list(AVAILABLE_ONLINE_PROVIDER_IDS)
    _manual_online_provider_ids = list(MANUAL_ONLINE_PROVIDER_IDS)
    _online_provider_ids = ["subhd", "zimuku"]
    _online_engine = DEFAULT_ENGINE
    _online_use_proxy = False
    _online_site_urls = dict(DEFAULT_PROVIDER_ROOTS)
    _online_rate_records: Dict[str, List[float]] = {}
    _online_rate_limit_per_minute = 5
    _auto_transfer_queue_debounce_seconds = 3
    _auto_transfer_queue_history_limit = 200
    _transfer_auto_dedupe_seconds = 300
    _transfer_auto_recent: Dict[str, float] = {}
    _transfer_auto_lock = threading.Lock()
    _auto_transfer_tasks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    _auto_transfer_worker: Optional[threading.Thread] = None
    _auto_transfer_stopping = False
    _auto_season_package_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    _embedded_subtitle_probe_cache: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    _embedded_subtitle_probe_cache_max_size = 500
    _embedded_subtitle_text_codecs = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
    _embedded_subtitle_image_codecs = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvb_teletext", "eia_608"}
    _assrt_api_key = ""
    _assrt_api_url = DEFAULT_ASSRT_API_URL
    _opensubtitles_api_key = ""
    _opensubtitles_api_url = DEFAULT_OPENSUBTITLES_API_URL
    _opensubtitles_username = ""
    _opensubtitles_password = ""
    _ai_link_enabled = True
    _traditional_to_simplified = False
    _auto_search_on_transfer = False
    _auto_skip_chinese_media_on_transfer = True
    _auto_transfer_subtitle_strategy = "online_then_ai_source"
    _auto_search_min_score = 20
    _trust_transfer_history_paths = False
    _manual_strm_enabled = False
    _manual_strm_paths = []
    _auto_search_on_manual_strm = False
    _auto_search_on_strm_transfer = True
    _strm_roots = ["/mnt/strm"]
    _strm_wait_seconds = 180
    _strm_scan_interval_seconds = 10
    _strm_job_queue = None
    _strm_worker = None
    _strm_stop_event = None
    _strm_recent_jobs = {}
    _manual_strm_watch_debounce_seconds = 2
    _timeline_max_offset_seconds = 120
    _timeline_min_offset_seconds = 0.2
    _timeline_vad_mode = "webrtc"
    _timeline_allow_risky_offset = False
    _cache_ttl_seconds = 1800
    _entry_map_max_size = 2000
    _media_index_cache_max_keys = 20
    _subtitle_directory_cache = OrderedDict()
    _subtitle_directory_cache_max_size = 1000
    _subtitle_directory_cache_ttl_seconds = 900
    _match_history_cache_ttl_seconds = 86400
    _match_history_validation_interval_seconds = 300
    _timeline_task_ttl_seconds = 86400
    _rar_dependency_status: Dict[str, Any] = {
        "mode": "none",
        "state": "idle",
        "message": "",
        "checked_at": "",
    }
    _entry_map: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    _media_index_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    _cache_refreshing = False
    _cache_refresh_started_at = ""
    _cache_refresh_completed_at = ""
    _cache_refresh_error = ""
    _local_entries_cache: Dict[str, Any] = {
        "loaded_at": None,
        "entries": [],
        "media_count": 0,
        "persisted": False,
    }
    _match_history_cache: Dict[str, Any] = {
        "version": 3,
        "loaded_at": None,
        "validated_at": None,
        "signature": "",
        "items": [],
        "entry_count": 0,
        "persisted": False,
    }
    _match_history_generation = 0
    _match_history_refreshing = False
    _match_history_refresh_started_at = ""
    _match_history_refresh_completed_at = ""
    _match_history_refresh_error = ""
    _match_history_refresh_lock = threading.Lock()
    _match_history_build_lock = threading.Lock()
    _timeline_tasks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    _tmdb_detail_cache: Dict[str, Dict[str, Any]] = {}

    _subtitle_exts = SUBTITLE_EXTS
    _archive_exts = {".zip", ".rar", ".7z"}
    _rar_exts = {".rar"}
    _sevenzip_exts = {".7z"}
    _rar_tools = ("unar", "/usr/bin/unar")
    _sevenzip_tools = ("unar", "/usr/bin/unar")
    _rar_python_package = "rarfile"
    _rar_dependency_modes = set(RAR_DEPENDENCY_MODES)
    _auto_transfer_subtitle_strategies = set(AUTO_TRANSFER_SUBTITLE_STRATEGIES)
    _auto_multi_subtitle_modes = set(AUTO_MULTI_SUBTITLE_MODES)
    _default_auto_language_priority = list(DEFAULT_AUTO_LANGUAGE_PRIORITY)
    _default_auto_format_priority = list(DEFAULT_AUTO_FORMAT_PRIORITY)
    _auto_transfer_subtitle_strategy_aliases = dict(AUTO_TRANSFER_SUBTITLE_STRATEGY_ALIASES)
    _chinese_media_language_codes = {"zh", "cn", "zho", "cmn", "yue"}
    _chinese_media_country_codes = {"cn", "hk", "tw", "sg"}
    _chinese_media_region_names = {
        "china",
        "hong kong",
        "taiwan",
        "singapore",
        "中国",
        "大陆",
        "香港",
        "台湾",
        "新加坡",
    }
    _chinese_media_category_pattern = re.compile(r"华语|国产|大陆|内地|香港|台湾|港剧|台剧|港片|台片|港台|中国")
    _stream_exts = {".strm"}
    _default_session_hours = 24
    _language_suffix_aliases = dict(LANGUAGE_SUFFIX_ALIASES)

    def init_plugin(self, config: dict = None):
        normalized_config = normalize_plugin_config(
            config,
            subtitle_exts=self._subtitle_exts,
            default_auto_language_priority=self._default_auto_language_priority,
            default_auto_format_priority=self._default_auto_format_priority,
            available_provider_ids=self._available_online_provider_ids,
            default_provider_ids=self._default_online_provider_ids,
        )
        apply_runtime_config(self, normalized_config)
        if (config or {}).get("opensubtitles_username") and "@" in self._normalize_text((config or {}).get("opensubtitles_username")):
            logger.warning("[StrmSubtitleAuto] OpenSubtitles 用户名疑似邮箱，已忽略下载认证用户名")
        sync_class_runtime_config(type(self), self)
        reset_runtime_state(self)
        # Do not scan the STRM tree on startup. Only new transfer events enter the worker.
        self.services.history().restore_persisted_match_history_cache()
        self._save_config()
        self._prepare_rar_dependency()
        self.services.upload_session().cleanup_old_sessions()
        self._start_strm_worker()

    def get_state(self) -> bool:
        return bool(self._enabled)

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_actions(self) -> List[Dict[str, Any]]:
        from .automation.workflow_actions import build_actions

        return build_actions(self)

    def get_agent_tools(self) -> List[type]:
        from .automation.agent_tools import get_agent_tools

        return get_agent_tools()

    def get_api(self) -> List[Dict[str, Any]]:
        return build_api_routes(self)

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return build_config_form(
            default_auto_language_priority=self._default_auto_language_priority,
            default_auto_format_priority=self._default_auto_format_priority,
            default_online_provider_ids=self._default_online_provider_ids,
        )

    def get_page(self) -> List[dict]:
        return []

    def get_sidebar_nav(self) -> List[Dict[str, Any]]:
        if not self.get_state() or not self._show_sidebar_nav:
            return []
        return [
            {
                "nav_key": "main",
                "title": "STRM 中文字幕自动处理",
                "icon": "mdi-file-upload-outline",
                "section": "organize",
                "permission": "manage",
                "order": 48,
            }
        ]

    @property
    def services(self) -> StrmSubtitleAutoServices:
        registry = getattr(self, "_services_registry", None)
        if registry is None:
            registry = StrmSubtitleAutoServices(self)
            self._services_registry = registry
        return registry

    def stop_service(self):
        self._stop_strm_worker()
        self.services.auto_transfer().stop()

    @eventmanager.register(EventType.TransferComplete)
    def listen_transfer_complete(self, event: MPEvent):
        if not self.get_state() or not self._auto_search_on_strm_transfer:
            return
        self._enqueue_strm_event(event)

    def _start_strm_worker(self) -> None:
        self._stop_strm_worker()
        self._strm_job_queue = queue.Queue()
        self._strm_stop_event = threading.Event()
        self._strm_worker = threading.Thread(target=self._strm_worker_loop, name="StrmSubtitleWorker", daemon=True)
        self._strm_worker.start()

    def _stop_strm_worker(self) -> None:
        if self._strm_stop_event:
            self._strm_stop_event.set()
        if self._strm_job_queue:
            try: self._strm_job_queue.put_nowait(None)
            except Exception: pass
        if self._strm_worker and self._strm_worker.is_alive():
            self._strm_worker.join(timeout=3)
        self._strm_worker = None
        self._strm_job_queue = None

    def _enqueue_strm_event(self, event: Any) -> None:
        if not self._strm_job_queue: return
        self._strm_job_queue.put(event)

    def _strm_worker_loop(self) -> None:
        while self._strm_stop_event and not self._strm_stop_event.is_set():
            try: event = self._strm_job_queue.get(timeout=1)
            except queue.Empty: continue
            if event is None: return
            try: self._process_strm_event(event)
            except Exception as exc: logger.exception("[StrmSubtitleAuto] STRM 字幕任务失败: %s", exc)

    def _process_strm_event(self, event: Any) -> None:
        data = getattr(event, "event_data", None) or {}
        entries = self.services.target_resolver().entries_from_transfer_event(data)
        for entry in entries:
            strm = self._wait_for_matching_strm(entry)
            if not strm: continue
            entry = dict(entry)
            entry.update({"path": str(strm), "filename": strm.name, "basename": strm.stem, "storage": "local", "writable": True, "relative_path": str(strm)})
            self.services.auto_transfer().auto_process_transfer_entry(entry)

    def _wait_for_matching_strm(self, entry: Dict[str, Any]) -> Optional[Path]:
        deadline = time.time() + int(self._strm_wait_seconds)
        while time.time() < deadline and not (self._strm_stop_event and self._strm_stop_event.is_set()):
            found = self._find_matching_strm(entry)
            if found: return found
            time.sleep(int(self._strm_scan_interval_seconds))
        logger.warning("[StrmSubtitleAuto] 等待 STRM 超时 title=%s", entry.get("title"))
        return None

    def _find_matching_strm(self, entry: Dict[str, Any]) -> Optional[Path]:
        title = self._normalize_text(entry.get("title")).casefold()
        year = self._normalize_text(entry.get("year"))
        season = self._safe_int(entry.get("season"), 0)
        episode = self._safe_int(entry.get("episode"), 0)
        best = None
        for root_text in self._strm_roots or []:
            root = Path(root_text)
            if not root.is_dir(): continue
            for path in root.rglob("*.strm"):
                name = path.name.casefold()
                if title and title not in name and title not in str(path.parent).casefold(): continue
                if year and year not in str(path.parent): continue
                if season and f"s{season:02d}" not in name and f"season {season}" not in str(path.parent).casefold(): continue
                if episode and f"e{episode:02d}" not in name: continue
                best = path
                break
            if best: break
        return best

    def _save_config(self) -> None:
        self.update_config(build_save_config_payload(self))


install_shell_helpers(
    StrmSubtitleAuto,
    upload_file_type=UploadFile,
    http_exception=HTTPException,
    settings_obj=settings,
)
