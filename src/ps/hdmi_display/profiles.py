"""Explicit business geometry; raster rates are not source-result rates."""
from dataclasses import dataclass

from .scanout import MODE_720P60, MODE_1080P30, MODE_1080P60, DisplayMode


@dataclass(frozen=True)
class BusinessProfile:
    name: str
    input_size: tuple[int, int]
    hdmi: DisplayMode
    dp: DisplayMode | None = None
    source_fps: int = 30

    def sr_size(self, scale):
        if isinstance(scale, bool) or scale not in (2, 4):
            raise ValueError('SR scale must be 2 or 4')
        return tuple(value * scale for value in self.input_size)

    def metadata(self):
        return {'name': self.name, 'input_size': list(self.input_size),
                'source_fps': self.source_fps, 'hdmi_mode': self.hdmi.name,
                'dp_mode': self.dp.name if self.dp else None,
                'sr_scales': [2, 4], 'camera_scale': 4}


LEGACY = BusinessProfile('legacy_hdmi_720p', (320, 180), MODE_720P60)
NEXYS = BusinessProfile('nexys_dp_bridge_v1', (480, 270), MODE_1080P30, MODE_1080P60)
PROFILES = {profile.name: profile for profile in (LEGACY, NEXYS)}


def get_profile(value=None):
    if value is None:
        return LEGACY
    if isinstance(value, BusinessProfile):
        if PROFILES.get(value.name) == value:
            return value
    elif isinstance(value, str) and value in PROFILES:
        return PROFILES[value]
    raise ValueError('Unknown business profile')


def manifest_profile(manifest):
    """Old 720p packages may omit the profile; new modes must declare it fully."""
    declaration = manifest.get('business_profile')
    if declaration is None:
        profile = LEGACY
    elif isinstance(declaration, dict):
        profile = get_profile(declaration.get('name'))
        if declaration != profile.metadata():
            raise ValueError('Business profile geometry or timing differs from its contract')
    else:
        raise ValueError('Invalid business profile declaration')
    if manifest.get('mode') != profile.hdmi.name:
        raise ValueError('Package HDMI mode and business profile disagree')
    return profile
