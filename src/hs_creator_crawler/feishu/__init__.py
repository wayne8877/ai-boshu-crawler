"""飞书 Base 客户端。

解耦原仓硬编码表名/schema。schema 走配置（CreatorTrackerConfig.feishu.tables），
允许每个部署独立映射字段名（新表名 / 加字段都只改配置不改代码）。
"""

from .client import FeishuBaseClient

__all__ = ["FeishuBaseClient"]
