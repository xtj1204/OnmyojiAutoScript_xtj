# This Python file uses the following encoding: utf-8
from typing import Any, Dict

from pydantic import Field, model_serializer, model_validator, ValidationError

from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.EvoZone.config import KirinType, Layer


class FriendBattleConfig(ConfigBase):
    """好友协战的全局参数：所有好友跑同一套麒麟/层数/限制。"""
    # 好友数量，决定下面生成多少张好友卡
    friend_count: int = Field(default=1, ge=1, description='friend_count_help')
    # 麒麟类型
    kirin_type: KirinType = Field(default=KirinType.LIGHTNINGKIRIN, description='kirin_type_help')
    # 挑战层数
    layer: Layer = Field(default=Layer.TEN, description='layer_help')
    # 限制时间
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 限制次数
    limit_count: int = Field(default=30, description='limit_count_help')


class FriendInfo(AccountInfo):
    """好友账号：在通用账号信息上增加只读的「协战状态」。

    [AI-2026-11-18] 用 str + type='status' 而不是枚举，OASX 才会渲染成只读文本；
    枚举会被渲染成可编辑下拉框。取值只有 未协战 / 已协战。
    """
    battle_status: str = Field(default='未协战', description='battle_status_help',
                               json_schema_extra={'type': 'status'})


class FriendBattle(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    friend_battle_config: FriendBattleConfig = Field(default_factory=FriendBattleConfig)
    # 执行完任务切回账号：角色名留空则不切回
    return_account: AccountInfo = Field(default_factory=AccountInfo, description='return_account_help')
    # 好友列表
    friend_list: list[FriendInfo] = None

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        """把配置里铺平的 friend_list_1..N 收回成列表，并按 friend_count 补齐。

        与 tasks/FindJade/config.py 的做法一致：OAS 保存时把列表展开成 xxx_1、xxx_2，
        读取时需要还原，否则界面上的分组会错位。
        """
        friend_count = v.get('friend_battle_config', {}).get('friend_count', 1)

        def validator_list(list_name, data, item_type=None, list_size=1):
            if list_name not in data:
                data[list_name] = []

            remove_keys = []
            for key, value in data.items():
                if list_name == key or list_name not in key:
                    continue
                try:
                    item = item_type(**value)
                    if item.is_valid():
                        data[list_name].append(item)
                    remove_keys.append(key)
                except ValidationError:
                    pass
                except TypeError:
                    pass

            for key in remove_keys:
                del data[key]

            if item_type is not None and len(data[list_name]) < list_size:
                for _ in range(list_size - len(data[list_name])):
                    data[list_name].append(item_type())

        validator_list('friend_list', v, FriendInfo, friend_count)

        return v

    @model_serializer()
    def serializer_model(self, value: Any) -> Dict[str, Any]:
        """把 friend_list 展开成 friend_list_1..N，供配置页按卡片分组显示。"""
        properties = self.__dict__
        data = {}

        def v_dump(v):
            try:
                return v.model_dump()
            except AttributeError:
                return v

        for key, value in properties.items():
            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data
