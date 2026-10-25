import json
import os
from dataclasses import dataclass, field
from aiortc import RTCConfiguration, RTCIceServer


def rtc_config(servers):
    return RTCConfiguration(iceServers=[RTCIceServer(**server) for server in servers])


@dataclass
class ServerConfig:
    mcp_token: str
    devices: dict[str, str]
    ice_servers: list = field(default_factory=list)

    def __post_init__(self):
        import re
        if len(self.mcp_token)<32 or not self.devices or len(self.devices)>32:
            raise ValueError('请配置至少 32 字符的 MCP Token 以及 1–32 个设备')
        for device, token in self.devices.items():
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',device) or not isinstance(token,str) or len(token)<32 or token==self.mcp_token:
                raise ValueError('设备 ID 无效或设备 Token 不独立/不足 32 字符')
        if len(set(self.devices.values()))!=len(self.devices):
            raise ValueError('每个设备必须使用独立 Token')
        rtc_config(self.ice_servers)

    @classmethod
    def from_env(cls):
        return cls(os.getenv('FILE_MCP_TOKEN',''),json.loads(os.getenv('FILE_DEVICES_JSON','{}')),
                   json.loads(os.getenv('FILE_ICE_SERVERS_JSON','[]')))
