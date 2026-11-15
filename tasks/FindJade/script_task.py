import importlib
from datetime import datetime, timedelta

from module.exception import TaskEnd, RequestHumanTakeover
from module.logger import logger
from module.task_loader import task_script_path
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.DailyTrifles.script_task import ScriptTask as DailyTriflesTask
from tasks.FindJade import WantedQuestsEx
from tasks.FindJade.assets import FindJadeAssets
from tasks.FindJade.config import AccountInfo, FindJade
from tasks.GameUi.game_ui import GameUi
from tasks.WantedQuests.config import CooperationSelectMask, CooperationType


class ScriptTask(GameUi, FindJadeAssets):
    fade_conf: FindJade= None

    # [AI-2026-11-15] 「邀请状态」用的协作类型文案，数字与 CooperationType 的值一致
    INVITE_TYPE_LABELS = ((1, '金协'), (2, '勾协'), (4, '粮协'), (8, '体协'))

    def _reset_invite_status(self):
        """清空本轮记录；_invite_ran 用于区分「跑了但没协作」和「一个号都没跑」"""
        self._invite_records = {}
        self._invite_ran = False

    def _collect_invite_status(self, wq):
        """把某个小号一轮的邀请结果并进来；已邀请优先于邀请失败"""
        self._invite_ran = True
        for item in (getattr(wq, 'last_invite_results', None) or []):
            name = item.get('name')
            try:
                ctype = int(item.get('type'))
            except (TypeError, ValueError):
                continue
            rec = self._invite_records.setdefault(name, {})
            if rec.get(ctype) is True:
                continue
            rec[ctype] = bool(item.get('success'))

    @staticmethod
    def _has_invite_success(wq) -> bool:
        return any(item.get('success') for item in (getattr(wq, 'last_invite_results', None) or []))

    def _flush_invite_status(self):
        """按每个大号启用的协作类型汇总成多行文案，写入 invite_status 供 OASX 展示"""
        for info in (self.fade_conf.invite_info_list or []):
            mask = CooperationSelectMask[info.default_invite_type.value]
            rec = self._invite_records.get(info.name, {})
            lines = []
            for ctype, label in self.INVITE_TYPE_LABELS:
                if not (ctype & mask):
                    continue
                if ctype in rec:
                    lines.append(f'{label}：{"已邀请" if rec[ctype] else "邀请失败"}')
                elif not info.need_invite(CooperationType(ctype)):
                    lines.append(f'{label}：无需邀请')
                else:
                    lines.append(f'{label}：无协作')
            info.invite_status = '\n'.join(lines)

    def _switch_back(self):
        """按 return_account 切回；失败重试 2 次仍不成功则交人工（游戏会停在登录页，继续跑会连累后续任务）"""
        account = self.fade_conf.return_account
        if account is None or not account.is_valid():
            return
        if not account.account:
            logger.warning('return_account 已填角色名但没填账号，无法切回')
            return
        for attempt in range(1, 4):
            logger.info('switch back to %s-%s (attempt %d)', account.character, account.svr, attempt)
            if SwitchAccount(self.config, self.device, account).switchAccount():
                logger.info('switch back success: %s-%s', account.character, account.svr)
                return
            logger.warning('switch back failed (attempt %d): %s-%s', attempt, account.character, account.svr)
        raise RequestHumanTakeover

    def run(self):
        self.fade_conf = self.config.find_jade
        self._reset_invite_status()
        switched = False

        for accountInfo in self.fade_conf.sup_account_list:
            logger.info("start %s-%s ", accountInfo.character, accountInfo.svr)
            if not self.is_need_login(accountInfo):
                logger.warning("%s Skipped last Login Time:%s", accountInfo.character, accountInfo.last_complete_time)
                continue
            suc = SwitchAccount(self.config, self.device, accountInfo).switchAccount()
            if not suc:
                logger.warning("switch to %s-%s Failed", accountInfo.character, accountInfo.svr)
                continue
            switched = True
            invited = False
            #
            wq = self.CreatObjectFromModule("WantedQuests", config=self.config, device=self.device)
            wq.fade_conf = self.fade_conf

            try:
                # [AI-2026-11-10] 从 runhey 的 harvest() 精简而来（原逻辑挂在 Restart/login.py 的
                # app_handle_login 里，每次登录都收菜；xtj 迁移到 LoginService 时未跟过来）。
                # 这里只作用于 FindJade 的小号：切号成功后先收邮件与庭院事务（含每日签到、一键完成），
                # 再跑协作。必须放在 wq.run() 之前——没有协作任务时它会直接 raise TaskEnd。
                dt = DailyTriflesTask(config=self.config, device=self.device)
                dt.run_pickup_email()
                dt.run_courtyard_affairs()
                wq.run()
            except TaskEnd as e:
                logger.warning("%s-%s TaskEnd", accountInfo.character, accountInfo.svr)
                # [AI-2026-11-15] 汇总本轮邀请结果，随配置一起落盘
                self._collect_invite_status(wq)
                self._flush_invite_status()
                invited = self._has_invite_success(wq)
                # 更新配置文件中的时间
                self.fade_conf.update_account_login_history(accountInfo)
                self.save_config()
            except RequestHumanTakeover as e:
                raise
            except Exception as e:
                logger.error(e)
                self.next_run("FindJade", success=False)

            # [AI-2026-11-15] 未勾选「邀请成功后继续切换小号」时，受邀成功就停止切号
            if invited and not self.fade_conf.find_jade_config.continue_after_invited:
                logger.info('邀请成功且未勾选「邀请成功后继续切换小号」，提前结束小号循环')
                break

        if getattr(self, '_invite_ran', False):
            self._flush_invite_status()
            self.save_config()

        # [AI-2026-11-15] 按配置切回原账号（留空则不切，保持原有行为）
        if switched:
            self._switch_back()

        self.next_run("FindJade", success=True)
        raise TaskEnd("FindJade")


    def is_need_login(self, item: AccountInfo):
        """
            根据上次登陆时间 判断是否需要登录查找
        @param item:
        @type item:
        """
        lastTime = item.last_complete_time
        now = datetime.now()
        if now - lastTime > timedelta(hours=13):
            return True
        if (lastTime.hour >= 18 or lastTime.hour < 5) and (18 > now.hour >= 5):
            return True
        if (5 <= lastTime.hour < 18) and now.hour >= 18:
            return True
        return False

    def CreatObjectFromModule(self, task_name: str, **kwargs):
        module_name = 'script_task'
        module_path = str(task_script_path(task_name))

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        WQEX = type("WQEX", (module.ScriptTask,), {
            "need_invite_vip": WantedQuestsEx.need_invite_vip,
            "get_invite_vip_name": WantedQuestsEx.get_invite_vip_name,
            "next_run": WantedQuestsEx.next_run,
            "invite_success_callback": WantedQuestsEx.invite_success_callback,
            "get_config": WantedQuestsEx.get_config
        })
        wq = WQEX(**kwargs)
        return wq

    def save_config(self):
        self.config.save()

    def next_run(self, task: str, finish: bool = False,
                 success: bool = None, server: bool = True, target: datetime = None) -> None:
        now = datetime.now()
        if success:
            if 5 <= now.hour < 18:
                self.set_next_run(task, target=now.replace(hour=18, minute=5))
            elif now.hour < 5:
                self.set_next_run(task, target=now.replace(hour=5, minute=5))
            else:
                self.set_next_run(task, target=now.replace(hour=18, minute=5) + timedelta(days=1))
        else:
            self.set_next_run(task, target=now + timedelta(minutes=10))


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    # from mypatch import SimplePatch

    # SimplePatch.patch()

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
