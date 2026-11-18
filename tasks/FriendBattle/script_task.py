# This Python file uses the following encoding: utf-8
from datetime import datetime

from module.exception import TaskEnd
from module.logger import logger
from tasks.GameUi.game_ui import GameUi


class ScriptTask(GameUi):
    """好友协战（骨架）。

    当前只做两件事：
    1. 按「自然日」判断每个好友今天是否已经跑过（当天跑过就跳过，次日再跑）
    2. 把轮到的好友的协战状态重置为「未协战」，然后按成功间隔排下一次

    真正的功能（切换小号 → 单人跑觉醒 → 完成置「已协战」）后续再实现。
    """

    def run(self):
        conf = self.config.friend_battle
        friends = conf.friend_list or []
        today = datetime.now().date()

        pending = []
        for info in friends:
            if info.last_complete_time.date() == today:
                logger.info('%s-%s 今天已执行，跳过', info.character, info.svr)
                continue
            pending.append(info)

        logger.info('好友协战：本次待执行 %s 个好友（骨架版本，不执行战斗）', len(pending))

        for info in pending:
            if info.battle_status != '未协战':
                info.battle_status = '未协战'
        self.config.save()

        self.next_run('FriendBattle', success=True)
        raise TaskEnd('FriendBattle')

    def next_run(self, task: str, success: bool = True):
        """按「成功/失败间隔」推算下一次：finish=True 表示以完成时间为基准。"""
        self.set_next_run(task, finish=True, success=success)
