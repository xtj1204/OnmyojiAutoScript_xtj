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

    @staticmethod
    def _has_invite_success(wq) -> bool:
        return any(item.get('success') for item in (getattr(wq, 'last_invite_results', None) or []))

    def _build_invite_status(self, wq) -> str:
        """汇总「当前这个小号」这一轮的邀请情况，写入该小号的 invite_status。

        - 面板上没出现该类型            -> 无协作
        - 邀请了但 get_invite_name 为空 -> 无需邀请（大号窗口内已被邀请过）
        - 出现但没走到邀请（类型被 break 截断）-> 未处理

        [AI-2026-11-19] 勾协区分现世/普通：结果与「出现过的类型」都按 (类型, 是否现世) 记录，
        只有出现或处理过现世时才多输出一行「勾协(现世)」，避免日常多一行噪音。
        """
        mask = CooperationSelectMask[(self.fade_conf.get_cooperation_type_mask()).value]
        seen_rw = {(int(t), bool(rw)) for t, rw in (getattr(wq, 'last_coop_realworld', None) or [])}
        # 兼容旧字段（只记类型，不知道现世与否）：这些一律按普通处理
        for t in (getattr(wq, 'last_coop_types', None) or []):
            try:
                seen_rw.add((int(t), False))
            except (TypeError, ValueError):
                continue

        results = {}
        for item in (getattr(wq, 'last_invite_results', None) or []):
            try:
                results[(int(item.get('type')), bool(item.get('realworld')))] = item.get('success')
            except (TypeError, ValueError):
                continue

        lines = []
        for ctype, label in self.INVITE_TYPE_LABELS:
            if not (ctype & mask):
                continue
            plain_key, realworld_key = (ctype, False), (ctype, True)
            has_plain = plain_key in results or plain_key in seen_rw
            has_realworld = realworld_key in results or realworld_key in seen_rw
            if has_plain or not has_realworld:
                lines.append(self._status_line(label, plain_key in results,
                                               results.get(plain_key), plain_key in seen_rw))
            if ctype == CooperationType.Jade and has_realworld:
                lines.append(self._status_line('勾协(现世)', realworld_key in results,
                                               results.get(realworld_key), realworld_key in seen_rw))
        return '\n'.join(lines)

    @staticmethod
    def _status_line(label: str, in_results: bool, success, seen: bool) -> str:
        if in_results:
            if success is None:
                return f'{label}：无需邀请'
            return f'{label}：已邀请' if success else f'{label}：邀请失败'
        if seen:
            return f'{label}：未处理'
        return f'{label}：无协作'

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
            # [AI-2026-11-19] 轮到该号就先清空邀请状态，跑完再赋新值：
            # 中途异常/被跳过时显示空，而不是残留上一轮的旧结果。
            if accountInfo.invite_status:
                accountInfo.invite_status = ''
                self.save_config()
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
            # [AI-2026-11-15] 排到「下一个时间段」开始，与 is_need_login 的分段保持一致：
            #   凌晨(0-5 仍属前一晚) -> 当天 05:05；早上段(5-18) -> 当天 18:05；晚上段(18-24) -> 次日 05:05
            if now.hour < 5:
                target = now.replace(hour=5, minute=5)
            elif now.hour < 18:
                target = now.replace(hour=18, minute=5)
            else:
                target = (now + timedelta(days=1)).replace(hour=5, minute=5)
            self.set_next_run(task, target=target)
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
