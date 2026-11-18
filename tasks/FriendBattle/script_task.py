# This Python file uses the following encoding: utf-8
from datetime import datetime

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.EvoZone.config import EvoZone, EvoZoneConfig, UserStatus
from tasks.EvoZone.script_task import ScriptTask as EvoZoneTask
from tasks.GameUi.game_ui import GameUi


class _EvoConfigProxy:
    """配置代理：让觉醒副本任务跑在「好友协战」的参数上。

    [AI-2026-11-18] 不改 tasks/EvoZone 的代码，只替换它读到的 evo_zone；
    其余属性（global_game / script / restart 等）原样转发给真实 config。
    """

    def __init__(self, real_config, evo_zone):
        self._real_config = real_config
        self.evo_zone = evo_zone

    def __getattr__(self, item):
        return getattr(self._real_config, item)


class ScriptTask(GameUi):
    """好友协战：逐个切换好友（小号）账号，单人刷觉醒，跑完即视为已协战。"""

    def run(self):
        conf = self.config.friend_battle
        friends = conf.friend_list or []
        today = datetime.now().date()

        pending = []
        for info in friends:
            if not info.is_valid():
                continue
            if info.last_complete_time.date() == today:
                logger.info('好友协战：%s-%s 今天已执行，跳过', info.character, info.svr)
                continue
            pending.append(info)

        if not pending:
            logger.info('好友协战：本轮没有需要执行的好友')
            self.next_run('FriendBattle', success=True)
            raise TaskEnd('FriendBattle')

        logger.info('好友协战：本轮待执行 %s 个好友', len(pending))
        all_success = True
        switched = False

        for info in pending:
            # [AI-2026-11-18] 按号清状态：轮到它才重置，避免把其他号上一轮的结果抹掉
            info.battle_status = '未协战'
            self.config.save()

            logger.info('好友协战：开始 %s-%s', info.character, info.svr)
            if not SwitchAccount(self.config, self.device, info).switchAccount():
                logger.warning('好友协战：切换 %s-%s 失败，跳过该号', info.character, info.svr)
                all_success = False
                continue
            switched = True

            try:
                count = self._run_evo_zone()
            except RequestHumanTakeover:
                raise
            except Exception as e:
                logger.error('好友协战：%s-%s 执行异常 %s', info.character, info.svr, e)
                all_success = False
                continue

            # 次数打满或时间到都算完成（用户确认：时间限制先到也算已协战）
            info.battle_status = '已协战'
            info.last_complete_time = datetime.now()
            self.config.save()
            logger.info('好友协战：%s-%s 已完成，觉醒 %s 次', info.character, info.svr, count)

        if switched:
            self._switch_back()

        self.next_run('FriendBattle', success=all_success)
        raise TaskEnd('FriendBattle')

    def _run_evo_zone(self) -> int:
        """用「好友协战」的参数跑一次觉醒副本，返回实际战斗次数。"""
        conf = self.config.friend_battle.friend_battle_config
        evo_zone = EvoZone()
        evo_zone.evo_zone_config = EvoZoneConfig(
            user_status=UserStatus.ALONE,   # 固定单人
            kirin_type=conf.kirin_type,
            layer=conf.layer,
            limit_time=conf.limit_time,
            limit_count=conf.limit_count,
            soul_buff_enable=False,         # 固定不开觉醒加成
        )

        task = EvoZoneTask(config=_EvoConfigProxy(self.config, evo_zone), device=self.device)
        # [AI-2026-11-18] 觉醒副本结束时会把 evo_zone 任务排到下次（set_next_run('EvoZone')），
        # 这里跑完不该动用户自己的觉醒副本排程，压成空操作。
        task.set_next_run = lambda *args, **kwargs: None

        try:
            task.run()
        except TaskEnd:
            pass
        return getattr(task, 'current_count', 0)

    def _switch_back(self):
        """按 return_account 切回；失败重试 2 次仍不成功则交人工（游戏会停在登录页，继续跑会连累后续任务）"""
        account = self.config.friend_battle.return_account
        if account is None or not account.is_valid():
            return
        if not account.account:
            logger.warning('好友协战：return_account 已填角色名但没填账号，无法切回')
            return
        for attempt in range(1, 4):
            logger.info('好友协战：切回 %s-%s（第 %d 次）', account.character, account.svr, attempt)
            if SwitchAccount(self.config, self.device, account).switchAccount():
                logger.info('好友协战：切回成功 %s-%s', account.character, account.svr)
                return
            logger.warning('好友协战：切回失败（第 %d 次）%s-%s', attempt, account.character, account.svr)
        raise RequestHumanTakeover

    def next_run(self, task: str, success: bool = True):
        """按「成功/失败间隔」推算下一次：finish=True 表示以完成时间为基准。"""
        self.set_next_run(task, finish=True, success=success)
