from datetime import datetime, timedelta
from typing import Any, Dict

from pydantic import Field, BaseModel, model_validator, model_serializer, ValidationError

from deploy.logger import logger
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime, dynamic_hide
from tasks.Component.config_scheduler import Scheduler
from tasks.WantedQuests.config import CooperationSelectMaskDescription, CooperationSelectMask, CooperationType


class InviteHistoryItem(BaseModel):
    cooperation_type: CooperationType
    time: DateTime


class InviteInfo(BaseModel):
    # 被邀请人员昵称
    name: str = Field(default="", description='name_help')
    default_invite_type: CooperationSelectMaskDescription = Field(default=CooperationSelectMaskDescription.JadeAndFood,
                                                                  description='default_invite_type_help')

    # 协作任务类型   上次邀请时间
    invite_history_1: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_1_help')
    invite_history_2: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_2_help')
    invite_history_4: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_4_help')
    invite_history_8: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_8_help')
    # [AI-2026-11-19] 现世勾协单独计时：大号可以同时挂现世勾协和普通勾协，两者互不占额度。
    # 界面上不显示（dynamic_hide），只在配置文件里保存。
    invite_history_jade_realworld: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                                    description='invite_history_jade_realworld_help')

    hide_fields = dynamic_hide('invite_history_jade_realworld')

    @staticmethod
    def _history_field(ctype: CooperationType, realworld: bool = False) -> str:
        """现世勾协用独立字段，其余类型沿用 invite_history_<类型值>。"""
        if ctype == CooperationType.Jade and realworld:
            return 'invite_history_jade_realworld'
        return "invite_history_" + str(ctype)

    def need_invite(self, ctype: CooperationType, realworld: bool = False):
        if not ctype & CooperationSelectMask[self.default_invite_type.value]:
            return False
        last_time = self.__getattribute__(self._history_field(ctype, realworld))

        now = datetime.now()
        if now - last_time > timedelta(hours=13):
            return True
        if (last_time.hour >= 18 or last_time.hour < 5) and (18 > now.hour >= 5):
            return True
        if (5 <= last_time.hour < 18) and now.hour >= 18:
            return True
        return False

    def update_invite_history(self, ctype: CooperationType, name=None, realworld: bool = False):
        if name != self.name:
            return
        self.__setattr__(self._history_field(ctype, realworld), datetime.now())

    def is_valid(self):
        return self.name != "" and self.name is not None


class JadeAccountInfo(AccountInfo):
    """FindJade 的小号配置：在通用账号信息上增加只读的「邀请状态」。

    [AI-2026-11-15] 状态按小号记录（哪个号刷出了协作、邀请成不成功），
    便于人工排查/修正；type='status' 让 OASX 走只读文本分支。
    """
    invite_status: str = Field(default="", description='invite_status_help',
                               json_schema_extra={'type': 'status'})


class FindJadeConfig(ConfigBase, extra='allow'):
    # 被邀请人数
    invite_info_count: int = Field(default=1, ge=1, description='invite_info_count_help')
    # 小号数
    sup_account_count: int = Field(default=1, ge=1, description='sup_account_count_help')
    # [AI-2026-11-15] 邀请成功后是否继续切小号。放在本组内而非 FindJade 顶层：
    # module/config/config_model.py 的 extract_groups 只支持顶层是模型或列表，标量会抛 KeyError。
    continue_after_invited: bool = Field(default=True, description='continue_after_invited_help')


class FindJade(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    find_jade_config: FindJadeConfig = Field(default_factory=FindJadeConfig)
    # [AI-2026-11-15] 执行完任务切回账号：角色名留空则不切回（沿用现有行为）。
    # 必须是顶层模型（$ref），配置页才会渲染成独立卡片。
    return_account: AccountInfo = Field(default_factory=AccountInfo, description='return_account_help')
    # 邀请人员信息
    invite_info_list: list[InviteInfo] = None
    # 小号信息
    sup_account_list: list[JadeAccountInfo] = None

    def get_invite_name(self, ctype: CooperationType, realworld: bool = False):
        for info in self.invite_info_list:
            if info.need_invite(ctype, realworld):
                return info.name
        return ""

    def update_invite_history(self, ctype: CooperationType, name: str, realworld: bool = False):
        # 更新邀请信息
        for info in self.invite_info_list:
            if info.name != name:
                continue
            info.update_invite_history(ctype, name, realworld)

    def update_account_login_history(self, account: AccountInfo):
        accountInfoList = self.sup_account_list
        for info in accountInfoList:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.last_complete_time = datetime.now()
            break

    def get_cooperation_type_mask(self) -> CooperationSelectMaskDescription:
        """
            根据配置中各个邀请人 的邀请类型
            综合得出该值
        """
        result = CooperationSelectMask.NoInvite
        for info in self.invite_info_list:
            result |= CooperationSelectMask[info.default_invite_type]
        name = CooperationSelectMask(result).name
        return CooperationSelectMaskDescription(name)

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        invite_info_count = v.get('find_jade_config', {}).get('invite_info_count', 1)
        sup_account_count = v.get('find_jade_config', {}).get('sup_account_count', 1)

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
                except ValidationError as e:
                    pass
                except TypeError as e:
                    pass

            for key in remove_keys:
                del data[key]

            if item_type is not None:
                if len(data[list_name]) < list_size:
                    for i in range(list_size - len(data[list_name])):
                        data[list_name].append(item_type())

        validator_list('invite_info_list', v, InviteInfo, invite_info_count)
        validator_list('sup_account_list', v, JadeAccountInfo, sup_account_count)

        return v

    @model_serializer()
    def serializer_model(self, value: Any) -> Dict[str, Any]:
        properties = self.__dict__
        data = {}
        # [AI-2026-11-19] 把序列化上下文透传给子模型，否则 dynamic_hide 在自定义序列化里会失效
        # （配置页拿 schema 时传的是 {'hide': True}，用来隐藏 invite_history_jade_realworld）。
        context = getattr(value, 'context', None)

        def v_dump(v):
            try:
                return v.model_dump(context=context) if context else v.model_dump()
            except AttributeError as e:
                logger.error(e)
                return v

        for key, value in properties.items():
            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data
