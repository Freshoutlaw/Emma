"""Map agent — resolves a region from a request and surfaces the region dashboard.

Whenever the operator asks something that needs a map ("show me a map of …",
"where is …", "weather in …"), this agent:

1. resolves the region against a curated dataset of real cities, falling back
   to **Open-Meteo geocoding** (real coordinates, country, timezone,
   population) for anything else,
2. flips the HUD to the full-screen ``map`` panel carrying that region as the
   display payload, and
3. answers with a one-line narration of the *real* facts.

The dashboard (``interfaces/hud/map.html``) shows **real data only**: an
OpenStreetMap map of the actual coordinates and live Open-Meteo weather. If a
region cannot be resolved (network gate closed, geocoding unavailable, unknown
place), the agent reports it honestly instead of inventing coordinates.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Optional

import httpx

from agents.base import AgentResult, BaseAgent

if TYPE_CHECKING:
    from agents.router import Pipeline

# ---------------------------------------------------------------- region data
# Curated cities — real coordinates, country, timezone and population.
# (name, aliases, lat, lon, country, iata-ish code, timezone, population_m)
_REGIONS: list[dict] = [
    # --- China
    dict(name="Shanghai", aliases=("上海", "shanghai", "shang hai", "pudong", "hongqiao"), lat=31.23, lon=121.47, country="China", code="SHA", tz="Asia/Shanghai", pop_m=26.9, desc="East China's coastal mega-hub and busiest port city"),
    dict(name="Beijing", aliases=("北京", "beijing", "peking", "china", "中国"), lat=39.90, lon=116.40, country="China", code="PEK", tz="Asia/Shanghai", pop_m=21.8, desc="Capital of China and its political/aviation nerve center"),
    dict(name="Shenzhen", aliases=("深圳", "shenzhen", "sz"), lat=22.54, lon=114.06, country="China", code="SZX", tz="Asia/Shanghai", pop_m=17.6, desc="Tech-manufacturing powerhouse on the Pearl River Delta"),
    dict(name="Guangzhou", aliases=("广州", "guangzhou", "canton"), lat=23.13, lon=113.26, country="China", code="CAN", tz="Asia/Shanghai", pop_m=18.7, desc="Southern gateway and the Pearl River Delta's trading heart"),
    dict(name="Hangzhou", aliases=("杭州", "hangzhou"), lat=30.27, lon=120.16, country="China", code="HGH", tz="Asia/Shanghai", pop_m=12.2, desc="E-commerce capital on the Qiantang River"),
    dict(name="Chengdu", aliases=("成都", "chengdu"), lat=30.57, lon=104.07, country="China", code="CTU", tz="Asia/Shanghai", pop_m=21.1, desc="Western China's aviation and tech hub"),
    dict(name="Chongqing", aliases=("重庆", "chongqing", "chungking"), lat=29.56, lon=106.55, country="China", code="CKG", tz="Asia/Shanghai", pop_m=32.1, desc="Mountain mega-city and inland logistics hub"),
    dict(name="Wuhan", aliases=("武汉", "wuhan"), lat=30.59, lon=114.31, country="China", code="WUH", tz="Asia/Shanghai", pop_m=12.4, desc="Nine-province thoroughfare on the Yangtze"),
    dict(name="Xi'an", aliases=("西安", "xian", "xi'an"), lat=34.34, lon=108.94, country="China", code="XIY", tz="Asia/Shanghai", pop_m=12.9, desc="Ancient capital and Silk Road origin"),
    dict(name="Nanjing", aliases=("南京", "nanjing", "nanking"), lat=32.06, lon=118.80, country="China", code="NKG", tz="Asia/Shanghai", pop_m=9.4, desc="Yangtze Delta manufacturing and research center"),
    dict(name="Tianjin", aliases=("天津", "tianjin", "tientsin"), lat=39.34, lon=117.36, country="China", code="TSN", tz="Asia/Shanghai", pop_m=13.9, desc="Northern port city and Beijing's maritime outlet"),
    dict(name="Qingdao", aliases=("青岛", "qingdao", "tsingtao"), lat=36.07, lon=120.38, country="China", code="TAO", tz="Asia/Shanghai", pop_m=10.1, desc="Port and brewing capital on the Yellow Sea"),
    dict(name="Xiamen", aliases=("厦门", "xiamen", "amoy"), lat=24.48, lon=118.09, country="China", code="XMN", tz="Asia/Shanghai", pop_m=5.2, desc="Island city facing the Taiwan Strait"),
    dict(name="Fuzhou", aliases=("福州", "fuzhou", "foochow"), lat=26.07, lon=119.30, country="China", code="FOC", tz="Asia/Shanghai", pop_m=8.4, desc="Capital of Fujian province"),
    dict(name="Hong Kong", aliases=("香港", "hong kong", "hk"), lat=22.32, lon=114.17, country="China SAR", code="HKG", tz="Asia/Hong_Kong", pop_m=7.5, desc="Global financial hub and one of the world's busiest airports"),
    # --- Asia-Pacific
    dict(name="Tokyo", aliases=("东京", "tokyo", "narita", "haneda", "japan", "日本"), lat=35.68, lon=139.69, country="Japan", code="NRT", tz="Asia/Tokyo", pop_m=37.4, desc="Japan's capital and largest metropolitan economy"),
    dict(name="Osaka", aliases=("大阪", "osaka"), lat=34.69, lon=135.50, country="Japan", code="KIX", tz="Asia/Tokyo", pop_m=19.1, desc="Kansai's commercial heart"),
    dict(name="Seoul", aliases=("首尔", "seoul", "korea", "south korea", "韩国"), lat=37.57, lon=126.98, country="South Korea", code="ICN", tz="Asia/Seoul", pop_m=9.7, desc="Capital of South Korea and Incheon's aviation hub"),
    dict(name="Singapore", aliases=("新加坡", "singapore", "sina", "sg"), lat=1.35, lon=103.82, country="Singapore", code="SIN", tz="Asia/Singapore", pop_m=5.9, desc="City-state and Southeast Asia's aviation crossroads"),
    dict(name="Bangkok", aliases=("曼谷", "bangkok"), lat=13.76, lon=100.50, country="Thailand", code="BKK", tz="Asia/Bangkok", pop_m=10.5, desc="Thailand's capital and tourism gateway"),
    dict(name="Kuala Lumpur", aliases=("吉隆坡", "kuala lumpur", "kl"), lat=3.14, lon=101.69, country="Malaysia", code="KUL", tz="Asia/Kuala_Lumpur", pop_m=8.2, desc="Malaysia's capital and KLIA hub"),
    dict(name="Taipei", aliases=("台北", "taipei", "taoyuan"), lat=25.03, lon=121.57, country="Taiwan", code="TPE", tz="Asia/Taipei", pop_m=7.0, desc="Taiwan's capital and high-tech center"),
    dict(name="Mumbai", aliases=("孟买", "mumbai", "bombay"), lat=19.08, lon=72.88, country="India", code="BOM", tz="Asia/Kolkata", pop_m=20.7, desc="India's financial capital"),
    dict(name="Delhi", aliases=("德里", "delhi", "new delhi", "india", "印度"), lat=28.61, lon=77.21, country="India", code="DEL", tz="Asia/Kolkata", pop_m=31.2, desc="India's capital territory"),
    dict(name="Sydney", aliases=("悉尼", "sydney", "australia", "澳大利亚"), lat=-33.87, lon=151.21, country="Australia", code="SYD", tz="Australia/Sydney", pop_m=5.3, desc="Australia's largest city and gateway"),
    dict(name="Melbourne", aliases=("墨尔本", "melbourne"), lat=-37.81, lon=144.96, country="Australia", code="MEL", tz="Australia/Melbourne", pop_m=5.1, desc="Southern Australian cultural capital"),
    dict(name="Dubai", aliases=("迪拜", "dubai"), lat=25.20, lon=55.27, country="United Arab Emirates", code="DXB", tz="Asia/Dubai", pop_m=3.5, desc="Gulf transit super-hub"),
    # --- Europe / Middle East / Africa
    dict(name="London", aliases=("伦敦", "london", "heathrow", "uk", "united kingdom", "britain", "英国"), lat=51.51, lon=-0.13, country="United Kingdom", code="LHR", tz="Europe/London", pop_m=9.5, desc="The UK's capital and Europe's busiest airspace"),
    dict(name="Paris", aliases=("巴黎", "paris", "france", "法国"), lat=48.86, lon=2.35, country="France", code="CDG", tz="Europe/Paris", pop_m=11.1, desc="France's capital and CDG hub"),
    dict(name="Frankfurt", aliases=("法兰克福", "frankfurt"), lat=50.11, lon=8.68, country="Germany", code="FRA", tz="Europe/Berlin", pop_m=0.8, desc="Germany's financial heart and continental air hub"),
    dict(name="Amsterdam", aliases=("阿姆斯特丹", "amsterdam"), lat=52.37, lon=4.90, country="Netherlands", code="AMS", tz="Europe/Amsterdam", pop_m=1.2, desc="Schiphol — a global airline hub"),
    dict(name="Madrid", aliases=("马德里", "madrid"), lat=40.42, lon=-3.70, country="Spain", code="MAD", tz="Europe/Madrid", pop_m=6.7, desc="Spain's capital"),
    dict(name="Moscow", aliases=("莫斯科", "moscow", "moskva", "russia", "俄罗斯"), lat=55.76, lon=37.62, country="Russia", code="SVO", tz="Europe/Moscow", pop_m=12.6, desc="Russia's capital"),
    dict(name="Rome", aliases=("罗马", "rome", "roma"), lat=41.90, lon=12.50, country="Italy", code="FCO", tz="Europe/Rome", pop_m=4.3, desc="Italy's capital"),
    dict(name="Istanbul", aliases=("伊斯坦布尔", "istanbul"), lat=41.01, lon=28.98, country="Türkiye", code="IST", tz="Europe/Istanbul", pop_m=15.8, desc="Continent-spanning transit hub"),
    dict(name="Cairo", aliases=("开罗", "cairo", "egypt", "埃及"), lat=30.04, lon=31.24, country="Egypt", code="CAI", tz="Africa/Cairo", pop_m=21.3, desc="Egypt's capital on the Nile"),
    # --- Americas
    dict(name="New York", aliases=("纽约", "new york", "nyc", "jfk", "usa", "united states", "america", "美国"), lat=40.71, lon=-74.01, country="United States", code="JFK", tz="America/New_York", pop_m=18.8, desc="America's largest metro and JFK gateway"),
    dict(name="Los Angeles", aliases=("洛杉矶", "los angeles", "la", "lax"), lat=34.05, lon=-118.24, country="United States", code="LAX", tz="America/Los_Angeles", pop_m=12.5, desc="West Coast's entertainment and aviation hub"),
    dict(name="San Francisco", aliases=("旧金山", "san francisco", "sf", "sfo", "bay area"), lat=37.77, lon=-122.42, country="United States", code="SFO", tz="America/Los_Angeles", pop_m=4.7, desc="Bay Area tech epicenter"),
    dict(name="Seattle", aliases=("西雅图", "seattle"), lat=47.61, lon=-122.33, country="United States", code="SEA", tz="America/Los_Angeles", pop_m=4.0, desc="Pacific Northwest hub"),
    dict(name="Chicago", aliases=("芝加哥", "chicago", "ord"), lat=41.88, lon=-87.63, country="United States", code="ORD", tz="America/Chicago", pop_m=9.5, desc="America's crossroads and O'Hare hub"),
    dict(name="Toronto", aliases=("多伦多", "toronto"), lat=43.65, lon=-79.38, country="Canada", code="YYZ", tz="America/Toronto", pop_m=6.3, desc="Canada's largest city"),
    dict(name="Mexico City", aliases=("墨西哥城", "mexico city", "cdmx", "mexico", "墨西哥"), lat=19.43, lon=-99.13, country="Mexico", code="MEX", tz="America/Mexico_City", pop_m=21.8, desc="Mexico's capital"),
    dict(name="São Paulo", aliases=("圣保罗", "sao paulo", "são paulo"), lat=-23.55, lon=-46.63, country="Brazil", code="GRU", tz="America/Sao_Paulo", pop_m=22.4, desc="Brazil's financial engine"),
    dict(name="Buenos Aires", aliases=("布宜诺斯艾利斯", "buenos aires"), lat=-34.60, lon=-58.38, country="Argentina", code="EZE", tz="America/Argentina/Buenos_Aires", pop_m=15.2, desc="Argentina's capital"),
]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())


def _standalone(alias: str) -> "re.Pattern":
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")


# Best-effort match: normalize (lowercase, strip punctuation/spaces) and pick
# the longest alias that appears in the request. Short aliases (like "la",
# "hk", "sz") must stand alone as whole tokens, or they false-positive inside
# other words (e.g. "atlantis" contains "la").
_ALIAS_INDEX: list[tuple[str, str, dict]] = []
for _r in _REGIONS:
    for _a in _r["aliases"]:
        _ALIAS_INDEX.append((_a.lower(), _normalize(_a), _r))


def match_region(message: str) -> Optional[dict]:
    """Return the region dict whose alias appears in the message, or None."""
    low = message.lower()
    norm = _normalize(message)
    best: Optional[dict] = None
    best_len = 0
    for raw, alias, region in _ALIAS_INDEX:
        if not alias or len(alias) <= best_len:
            continue
        if len(alias) < 4:
            # short alias: must be a whole token in the raw text
            if _standalone(raw).search(low):
                best, best_len = region, len(alias)
        elif alias in norm:
            best, best_len = region, len(alias)
    return best


# Lightweight phrase extractor so unknown regions still get a name when no
# LLM provider is available (e.g. "weather in atlantis" -> "Atlantis").
_REGION_PATTERNS = [
    re.compile(
        r"(?:weather|time|flights?|traffic|delays?|population|capital|map|location|coordinates|geography|status|route|nearest|distance)\s+?(?:of|to|in|at|for|from)?\s*?([a-z][a-z\s'\-\u00c0-\u024f]{1,40}?)(?=[,.;!?]|$)",
        re.IGNORECASE,
    ),
    re.compile(r"where\s+(?:is|are|'s)\s+(?:the\s+)?([a-z][a-z\s'\-\u00c0-\u024f]{1,40}?)(?=[,.;!?]|$)", re.IGNORECASE),
    re.compile(r"show\s+(?:me\s+)?(?:a\s+)?map\s+(?:of|for)?\s*?([a-z][a-z\s'\-\u00c0-\u024f]{1,40}?)(?=[,.;!?]|$)", re.IGNORECASE),
]


def extract_region_name(request: str) -> Optional[str]:
    """Best-effort region name from the request phrase, or None."""
    for pattern in _REGION_PATTERNS:
        match = pattern.search(request)
        if match:
            return match.group(1).strip()
    return None


_REGION_EXTRACT_PROMPT = (
    "You are Emma's geography resolver. From the user's request, extract the "
    "single region / location / city / country they are asking about. "
    'Return ONLY JSON: {"region": "<name or null if none>"}. '
    'Example: "show me a map of london" -> {"region": "london"}'
)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


def _curated_region(region: dict) -> dict:
    """Normalize a curated region into the public payload shape."""
    return {
        **region,
        "resolved": True,
        "source": "curated dataset",
    }


def _unresolved_region(name: str) -> dict:
    """Honest fallback — no invented coordinates, no invented population."""
    return {
        "name": name.strip().title() or "?",
        "country": None,
        "code": None,
        "lat": None,
        "lon": None,
        "tz": None,
        "pop_m": None,
        "desc": "Region could not be resolved (unknown place or network unavailable).",
        "resolved": False,
        "source": None,
    }


class MapAgent(BaseAgent):
    name = "map"
    description = "Resolves regions with real data and surfaces the region dashboard."

    # ---------------------------------------------------------------- extract
    async def _extract_name(self, request: str) -> Optional[str]:
        """Region name from the request: LLM first (fast path), phrase extractor fallback."""
        if self.pipeline.llm.route() != "none":
            try:
                text = await asyncio.wait_for(
                    self.pipeline.llm.complete(
                        [
                            {"role": "system", "content": _REGION_EXTRACT_PROMPT},
                            {"role": "user", "content": request},
                        ],
                        temperature=0.0,
                        max_tokens=80,
                    ),
                    timeout=12,
                )
                parsed = re.search(r"\{.*\}", text, re.DOTALL)
                if parsed:
                    data = json.loads(parsed.group(0))
                    name = str(data.get("region") or "").strip()
                    if name and name.lower() not in ("null", "none", "unknown"):
                        return name
            except Exception:
                pass
        return extract_region_name(request)

    async def _geocode(self, name: str) -> Optional[dict]:
        """Real coordinates/country/timezone/population via Open-Meteo geocoding."""
        if self.pipeline.network_gate is not None and not self.pipeline.network_gate.is_open:
            return None
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(
                    _GEOCODE_URL,
                    params={"name": name, "count": 1, "language": "en", "format": "json"},
                )
                response.raise_for_status()
                results = (response.json() or {}).get("results") or []
        except Exception:
            return None
        if not results:
            return None
        top = results[0]
        try:
            lat = round(float(top["latitude"]), 4)
            lon = round(float(top["longitude"]), 4)
        except (KeyError, TypeError, ValueError):
            return None
        pop = top.get("population")
        return {
            "name": str(top.get("name") or name).title(),
            "aliases": (name.lower(),),
            "lat": lat,
            "lon": lon,
            "country": str(top.get("country") or "—"),
            "code": str(top.get("country_code") or "—").upper(),
            "tz": str(top.get("timezone") or "UTC"),
            "pop_m": round(pop / 1_000_000, 1) if isinstance(pop, (int, float)) and pop > 0 else None,
            "desc": "Resolved via Open-Meteo geocoding (real coordinates).",
            "resolved": True,
            "source": "Open-Meteo geocoding",
        }

    async def _resolve_region(self, request: str) -> dict:
        matched = match_region(request)
        if matched:
            return _curated_region(matched)
        name = await self._extract_name(request)
        if name:
            matched = match_region(name)
            if matched:
                return _curated_region(matched)
            geocoded = await self._geocode(name)
            if geocoded:
                return geocoded
            return _unresolved_region(name)
        return _unresolved_region(request)

    # ---------------------------------------------------------------- run
    async def run(self, request: str) -> AgentResult:
        region = await self._resolve_region(request)
        payload = {
            "region": {
                "name": region["name"],
                "country": region.get("country"),
                "code": region.get("code"),
                "lat": region.get("lat"),
                "lon": region.get("lon"),
                "tz": region.get("tz"),
                "pop_m": region.get("pop_m"),
                "desc": region.get("desc"),
                "resolved": region.get("resolved", False),
                "source": region.get("source"),
            }
        }
        self.pipeline.display.set(
            "map",
            reason=f"region query: {region['name']}",
            payload=payload,
        )
        self._audit("map.displayed", action="map", detail={"region": region["name"], "resolved": region.get("resolved", False)})

        if not region.get("resolved"):
            narration = (
                f"🗺 Couldn't resolve real coordinates for “{region['name']}” — "
                "the network may be closed or the place unknown. The dashboard "
                "shows the facts we could establish and no invented data."
            )
        else:
            parts = [f"🗺 Showing the region dashboard for {region['name']}"]
            if region.get("country") and region["country"] != "—":
                parts.append(region["country"])
            if region.get("lat") is not None:
                parts.append(f"{region['lat']}, {region['lon']}")
            if region.get("pop_m"):
                parts.append(f"population ≈ {region['pop_m']}M")
            if region.get("tz"):
                parts.append(region["tz"])
            narration = " · ".join(parts) + (
                f". Resolved via {region.get('source', 'real data')}. The map is "
                "OpenStreetMap and the weather is live Open-Meteo data."
            )
        return AgentResult(ok=True, output=narration, intent="map")
