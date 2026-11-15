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
from tasks.WantedQuests.config import CooperationSelectMask


class ScriptTask(GameUi, FindJadeAssets):
    fade_conf: FindJade= None

    # [AI-2026-11-15] 「邀请状态」用的协作类型文案，数字与 CooperationType 的值一致
    INVITE_TYPE_LABELS = ((1, '金协'), (2, '勾协'), (4, '粮协'), (8, '体协'))

    @staticmethod
    def _has_invite_success(wq) -> bool:
        return any(item.get('success') for item in (getattr(wq, 'last_invite_results', None) or []))

    def _build_invite_status(self, wq) -> str:
        """汇总「当前这个小号」这一轮的邀请情况，写入该小号的 invite_status。

        - 面板上没出现该类型            -> 无协作
        - 邀请了但 get_invite_name 为空 -> 无需邀请（大号 13 小时内已被邀请过）
        - 出现但没走到邀请（类型被 break 截断）-> 未处理
        """
        mask = CooperationSelectMask[(self.fade_conf.get_cooperation_type_mask()).value]
        seen = {int(t) for t in (getattr(wq, 'last_coop_types', None) or [])}
        results = {}
        for item in (getattr(wq, 'last_invite_results', None) or []):
            try:
                results[int(item.get('type'))] = item.get('success')
            except (TypeError, ValueError):
                continue
        lines = []
        for ctype, label in self.INVITE_TYPE_LABELS:
            if not (ctype & mask):
                continue
            if ctype in results:
                success = results[ctype]
                if success is None:
                    lines.append(f'{label}：无需邀请')
                elif success:
                    lines.append(f'{label}：已邀请')
                else:
                    lines.append(f'{label}：邀请失败')
            elif ctype in seen:
                lines.append(f'{label}：未处理')
            else:
                lines.append(f'{label}：无协作')
        return '\n'.join(lines)

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
                # [AI-2026-11-15] 把本轮结果写到「这个」小号的邀请状态上，随配置一起落盘
                accountInfo.invite_status = self._build_invite_status(wq)
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

        # [AI-2026-11-15] 按配置切回原账号（留空则不切，保持原有行为）
        if switched:
            self._switch_back()

        self.next_run("FindJade", success=True)
        raise TaskEnd("FindJade")


    # [AI-2026-11-15] 冷却按「时间段」判定：5:00-18:00 为一段，18:00-次日 5:00 为另一段。
    # 同一段内执行过的号不再重复登录，跨段才允许（与 next_run 的 5:05/18:05 排程一致）。
    @staticmethod
    def _time_slot_key(dt: datetime) -> tuple:
        """18:00-次日5:00 归属起始日，所以凌晨 0-5 点回退一天，避免跨天被误判成同一段。"""
        if dt.hour < 5:
            return (dt.date() - timedelta(days=1), 1)
        return (dt.date(), 0 if dt.hour < 18 else 1)

    def is_need_login(self, item: AccountInfo):
        """
            根据上次登陆时间所在时间段判断是否需要登录查找
        @param item:
        @type item:
        """
        return self._time_slot_key(datetime.now()) != self._time_slot_key(item.last_complete_time)

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
