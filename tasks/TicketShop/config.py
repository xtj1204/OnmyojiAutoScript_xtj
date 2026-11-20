# This Python file uses the following encoding: utf-8
from typing import Any, Dict

from pydantic import Field, model_serializer, model_validator, ValidationError

from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class TicketShopConfig(ConfigBase):
    """寄售券购买的全局参数。"""
    # 小号数量，决定下面生成多少张小号卡
    sup_account_count: int = Field(default=1, ge=1, description='ticket_shop_sup_account_count_help')
    # 勾玉门槛：低于这个值直接跳过（买满 10 次需要 100 勾玉）
    jade_threshold: int = Field(default=100, ge=0, description='ticket_shop_jade_threshold_help')


class TicketAccountInfo(AccountInfo):
    """寄售券购买的小号：在通用账号信息上增加只读的「兑换状态」。

    [AI-2026-11-20] 用 str + type='status'，OASX 才会渲染成只读文本。
    取值：已兑换 / 勾玉不足(xx) / 购买失败(剩余 N) / 切换失败 / 执行异常(...)
    """
    ticket_status: str = Field(default="", description='ticket_status_help',
                               json_schema_extra={'type': 'status'})


class TicketShop(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    ticket_shop_config: TicketShopConfig = Field(default_factory=TicketShopConfig)
    # 执行完任务切回账号：角色名留空则不切回
    return_account: AccountInfo = Field(default_factory=AccountInfo, description='ticket_shop_return_account_help')
    # 小号列表
    sup_account_list: list[TicketAccountInfo] = None

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        """把配置里铺平的 sup_account_list_1..N 收回成列表，并按 sup_account_count 补齐。

        与 tasks/FindJade/config.py 的做法一致：OAS 保存时把列表展开成 xxx_1、xxx_2，
        读取时需要还原，否则界面上的分组会错位。
        """
        sup_account_count = v.get('ticket_shop_config', {}).get('sup_account_count', 1)

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

        validator_list('sup_account_list', v, TicketAccountInfo, sup_account_count)

        return v

    @model_serializer()
    def serializer_model(self, value: Any) -> Dict[str, Any]:
        """把 sup_account_list 展开成 sup_account_list_1..N，供配置页按卡片分组显示。"""
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
