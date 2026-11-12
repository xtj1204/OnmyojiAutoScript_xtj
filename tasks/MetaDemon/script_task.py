# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import random
from datetime import datetime

import cv2

from module.atom.click import RuleClick
from module.atom.image import RuleImage

from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.MetaDemon.config import MetaDemon, BossType
from tasks.MetaDemon.assets import MetaDemonAssets
import tasks.MetaDemon.page as ipages

from module.logger import logger
from module.exception import TaskEnd


class ScriptTask(GeneralBattle, SwitchSoul, GameUi, MetaDemonAssets):
    """超鬼王"""

    class State:
        done: bool = False
        switch_soul_done: bool = False
        synthesis_done: bool = False

    conf: MetaDemon = None
    cur_boss_type: BossType = None
    total_count: int = 0

    def _meta_demon_battle_key(self) -> str:
        """返回当前鬼王类型对应的通用战斗分组键。"""

        if self.cur_boss_type is None:
            return "meta_demon_default"
        return f"meta_demon_{self.cur_boss_type.name.lower()}"

    @property
    def enable_powerful_fire(self):
        powerful_list = self.conf.meta_demon_config.powerful_list_v
        return self.cur_boss_type is not None and self.cur_boss_type in powerful_list

    def before_run(self):
        self.navigator.add_unknown_closer(self.I_MD_GET_YESTERDAY_REWARD)
        self.conf = self.config.meta_demon
        self.limit_time = self.conf.meta_demon_config.limit_time_v
        self.limit_count = self.conf.meta_demon_config.limit_count
        page_shikigami_records = self.navigator.resolve_page(ipages.page_shikigami_records)
        page_meta_demon_boss = self.navigator.resolve_page(ipages.page_meta_demon_boss)
        page_main = self.navigator.resolve_page(ipages.page_main)
        if page_shikigami_records is None or page_meta_demon_boss is None or page_main is None:
            raise RuntimeError("MetaDemon 页面 session 初始化失败")

        # 仅修改当前任务 session 内的跳转图，不污染全局页面定义
        page_shikigami_records.clear_transitions()
        page_shikigami_records.connect(
            page_meta_demon_boss,
            self.I_UI_BACK_YELLOW,
            key="page_shikigami_records->page_meta_demon_boss",
        )
        page_meta_demon_boss.connect(
            page_shikigami_records,
            MetaDemonAssets.I_MD_SHIKIGAMI,
            key="page_meta_demon_boss->page_shikigami_records",
        )
        page_main.remove_transition(key="page_main->page_shikigami_records")

    def run(self):
        self.before_run()
        self.goto_page(ipages.page_meta_demon_boss)
        while True:
            self.update_global_state()
            if self.State.done:
                break
            self.screenshot()
            current_page = self.get_current_page()
            match current_page:
                case ipages.page_meta_demon_boss:
                    if self.check_and_prepare_battle():
                        self.start_battle()
                case ipages.page_battle:
                    self.battle_wait(self.conf.general_battle.random_click_swipt_enable)
                case ipages.page_reward:
                    self.click(ipages.reward_random_click(), interval=0.6)
                case ipages.page_battle_result:
                    self.click(ipages.random_click(), interval=0.6)
                case _:
                    time.sleep(random.uniform(0.7, 1.4))
        self.set_next_run(task="MetaDemon", success=True)
        self.finish_task()

    def start_battle(self):
        logger.hr('Click fire, start battle')
        while True:
            self.screenshot()
            if self.is_in_battle(False):
                self.total_count += 1
                self.run_general_battle(
                    self.conf.switch_soul.get_general_battle_conf(self.cur_boss_type),
                    battle_key=self._meta_demon_battle_key(),
                )
                break
            if self.appear(self.I_MD_DRINK_TEA, interval=1.2):  # 出现喝茶弹窗
                if self.conf.meta_demon_config.auto_tea:
                    self.click(self.I_MD_DRINK_TEA)  # 喝茶
                    continue
                # 不喝茶则结束任务安排下一次运行
                self.ui_click_until_disappear(self.I_MD_CLOSE_POPUP, interval=0.6)
                self.set_next_run('MetaDemon', target=datetime.now() + self.conf.scheduler.wait_interval)
                self.finish_task()
            if self.appear_then_click(self.I_MD_FIRE, interval=0.8):
                continue

    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info(f"Start battle process on {self.cur_boss_type.name if self.cur_boss_type else 'None'}")
        win = False
        while True:
            self.screenshot()
            if self.appear(self.I_MD_SUMMON_BOSS, interval=0.8) or \
                    self.appear(self.I_MD_SWITCH_TICKET, interval=0.8) or \
                    self.appear(self.I_MD_FIRE, interval=0.8):
                break
            if self.appear(self.I_WIN, interval=0.8):
                win = True
                self.click(ipages.random_click())
                continue
            if self.appear(self.I_FALSE, interval=0.8):
                win = False
                self.click(ipages.random_click())
                continue
            if self.appear(self.I_MD_SETTLING, interval=3.5):
                logger.info('wait result')
                time.sleep(random.uniform(3, 5))
                continue
        total_run_time = datetime.now() - self.start_time
        logger.info(f'battle win: {win}')
        logger.info(f'battle count: {self.total_count}/{self.limit_count}')
        logger.info(f'time count: {total_run_time.total_seconds():.1f}s/{self.limit_time.total_seconds()}s')
        return win

    def update_global_state(self):
        if datetime.now() - self.start_time >= self.limit_time or \
                self.total_count >= self.limit_count:
            self.State.done = True

    def check_and_prepare_battle(self) -> bool:
        """检查并做战斗前的准备工作
        :return: True可以战斗, False不能战斗
        """
        logger.hr('battle prepare', 2)
        if not self.check_fatigue():
            return False
        self.synthesis_boss_ticket()
        return self.check_and_switch_ticket() and self.check_and_switch_soul() and self.check_and_switch_powerful()

    def synthesis_boss_ticket(self):
        """合成鬼王"""
        if self.State.synthesis_done:
            return
        logger.hr('synthesis boss ticket')
        self.State.synthesis_done = True
        if len(self.conf.meta_demon_config.synthesis_list_v) <= 0:
            return
        type_ticket_dict: dict[BossType, tuple[str, int]] = {
            BossType.ONE_STAR: ("一星裂变之眼", 3),
            BossType.TWO_STARS: ("二星裂变之眼", 3),
            BossType.THREE_STARS: ("三星裂变之眼", 3),
            BossType.FOUR_STARS: ("四星裂变之眼", 3),
            BossType.FIVE_STARS: ("五星裂变之眼", 2),
        }
        synthesis_list = self.conf.meta_demon_config.synthesis_list_v
        self.ui_click(self.I_MD_SYNTHESIS, self.I_MD_START_SYNTHESIS, interval=0.6)
        for boss_type in synthesis_list:
            self.do_synthesis(type_ticket_dict[boss_type][0], type_ticket_dict[boss_type][1])
        self.ui_click(self.I_MD_CLOSE_POPUP, self.I_MD_SHIKIGAMI, interval=0.6)  # 关闭弹窗退回到鬼王界面

    def do_synthesis(self, star_name: str, need_ticket: int):
        """开始合成鬼王
        :param star_name: 目标星级行的文字，如 "一星裂变之眼"
        :param need_ticket: 合成当前级别鬼王需要的门票数量
        """
        # [AI-2026-11-11] 合成列表为动态列表：材料为0的星级整行消失、列表上移，
        # 固定坐标的图标模板会把下一星级误判为当前星级（一星模板在二星图标上仍有0.980分），
        # 导致一星材料用完后误点二星、触发合成券不足的礼包商店。改为按行文字精确定位，
        # 且每轮重新定位（本轮合成耗完材料后行会消失，缓存的坐标会落到下一个星级上）。
        click_cnt, max_cnt = 0, random.randint(2, 3)
        while True:
            self.screenshot()
            if click_cnt >= max_cnt:  # 兜底,多次点击开始合成都没有出现获得奖励则退出
                break
            if self.appear(self.I_MD_SYNTHESIS_NEED_MONEY, interval=0.8):  # 出现花钱购买合成券
                # [AI-2026-11-11] 新 UI 弹的是礼包商店，✕ 与通用弹窗位置不同，改用商店专用关闭按钮
                self.ui_click(self.I_MD_CLOSE_SHOP, self.I_MD_START_SYNTHESIS, interval=0.6)  # 关闭购买弹窗
                break
            if self.appear(self.I_UI_REWARD, interval=0.8):  # 出现获得奖励说明合成成功
                self.click(ipages.random_click(), interval=0.6)
                click_cnt = 0  # 清空点击标志
                continue
            if self.appear(self.I_MD_SYNTHESIS_EMPTY, interval=0.8):  # 有空位
                empty_list = self.I_MD_SYNTHESIS_EMPTY.match_all(self.device.image, frame_id=self.device.image_frame_id)
                if len(empty_list) < need_ticket:  # 空位数量小于需要的门票数量->门票不够则退出
                    logger.info(f'{star_name} ticket not enough, skip synthesis')
                    break
            coord = self.L_MD_SYNTHESIS_LIST.ocr_appear(self.device.image, star_name)
            if not isinstance(coord, tuple):
                logger.info(f'{star_name} not found, skip synthesis')
                break
            x, y = int(coord[0]), int(coord[1])
            row_click = RuleClick(roi_front=(x - 2, y - 2, 5, 5), roi_back=(x - 2, y - 2, 5, 5),
                                  name=f'md_synthesis_{star_name}')
            if self.click(row_click, interval=2):  # 点击对应星级行
                # [AI-2026-11-11] 等游戏处理完选中再点开始合成；否则同帧缓存下会抢跑空点一次，
                # 白耗一次 max_cnt 机会，导致本轮只合成成功一次就退出
                time.sleep(0.5)
                self.screenshot()
                if self.appear_then_click(self.I_MD_START_SYNTHESIS, interval=2.5):  # 点击开始合成
                    click_cnt += 1
                continue
        logger.info(f'{star_name} synthesis done')

    def check_fatigue(self):
        logger.hr('Check fatigue')
        current, remain, total = self.O_MD_FATIGUE.ocr_digit_counter(self.device.image)
        # 不喝茶且当前疲劳度已满
        if not self.conf.meta_demon_config.auto_tea and (current > total or remain < 0):
            self.set_next_run('MetaDemon', target=datetime.now() + self.conf.scheduler.wait_interval)
            self.finish_task()
        return True

    def check_and_switch_ticket(self):
        """切换鬼王门票并更新当前鬼王级别"""
        logger.hr('Summon boss')
        # 打开切换门票弹窗
        while True:
            self.screenshot()
            if self.appear(self.I_MD_FIRE, interval=1.2):  # 可以直接挑战鬼王则退出
                return True
            if self.appear(self.I_MD_GET_BOSS, interval=0.6):  # 打开弹窗了
                break
            if self.appear(self.I_MD_TICKET_EMPTY, interval=0.6):  # 任何门票都没有了
                self.State.done = True
                logger.info('Not have any boss ticket, exit')
                return False
            if self.appear_then_click(self.I_MD_SWITCH_TICKET, interval=0.6):
                continue
        # [AI-2026-11-12] 门票改用卡面星标识别：一二星票图标同款（卡面相似度 0.949），
        # 旧模板会互相误匹配，导致拿二星票当一星票用（星级判错→强力追击不开）
        tickets = []
        for attempt in range(3):
            time.sleep(0.4)  # 弹窗有展开动画，等渲染稳定再识别
            self.screenshot()
            tickets = self.read_ticket_stars()
            if len(tickets) > 0:
                break
            logger.info(f'Retry reading ticket stars ({attempt + 1}/3)')
        if len(tickets) <= 0:
            self.State.done = True
            logger.info('No boss ticket recognized, exit')
            self.ui_click_until_disappear(self.I_MD_CLOSE_POPUP, interval=0.6)
            return False
        # 获取所有存在的鬼王门票
        can_fire_list = [star for star, _, _ in tickets]
        # 根据存在的鬼王门票和配置的鬼王序列过滤出需要召唤的鬼王门票
        need_fire_list = [boss_type for boss_type in self.conf.meta_demon_config.fire_sequence_v if
                          boss_type in can_fire_list]
        if len(need_fire_list) <= 0:
            self.State.done = True
            logger.info('There is no boss that needs to be attacked, exit')
            self.ui_click_until_disappear(self.I_MD_CLOSE_POPUP, interval=0.6)
            return False
        # 第一次进来或上次一鬼王类型与这一次不同, 则需要切换御魂和预设
        if self.cur_boss_type is None or self.cur_boss_type != need_fire_list[0]:
            if not self.conf.switch_soul.switch_once:
                self.State.switch_soul_done = False
            self.current_count = 0
        self.cur_boss_type = need_fire_list[0]  # 更新当前鬼王级别
        logger.info(f'Summon {self.cur_boss_type.name.lower()} boss')
        target_card = next(item for item in tickets if item[0] == self.cur_boss_type)
        self.device.click(target_card[1], target_card[2],
                          control_name=f'md_ticket_{self.cur_boss_type.name.lower()}')  # 选中目标门票
        self.ui_click_until_disappear(self.I_MD_GET_BOSS, interval=0.6)  # 立即召唤鬼王
        for _ in range(20):
            self.screenshot()
            if self.appear(self.I_MD_FIRE, interval=1.0):  # 召唤完成回到鬼王界面
                return True
        logger.warning('Summon boss failed, challenge button not found')
        return False

    def read_ticket_stars(self) -> list:
        """读取切换门票弹窗里每张票的星级
        :return: [(BossType, 点击x, 点击y), ...]，按从左到右排序
        """
        ox, oy = self.O_MD_TICKET_OWNED.roi[0], self.O_MD_TICKET_OWNED.roi[1]
        tickets = []
        for item in self.O_MD_TICKET_OWNED.detect_and_ocr(self.device.image):
            text = item.ocr_text.replace('：', ':')
            if '持有' not in text:
                continue
            left = int(item.box[0, 0]) + ox
            right = int(item.box[1, 0]) + ox
            # 星标行在票卡下部、与「持有」文字水平居中对齐
            row_x = int(round((left + right) / 2 - 32))
            # [AI-2026-11-12] 主识别用门票模板的底部星标条在该卡范围内匹配（实测 0.997，
            # 对定位偏差 ±8px 不敏感）；颜色计数对定位敏感，降为兜底
            star = self.read_star_by_icon(left - 6, right + 6)
            if star is None:
                star = self.read_star_row(row_x)
                if star is not None:
                    logger.info(f'Boss ticket star by row color: {star.name.lower()} at x={row_x}')
            if star is None:
                logger.warning(f'Ticket star not recognized at x={row_x}')
                continue
            tickets.append((star, row_x + 32, 332))  # 点击落在卡片中部
        tickets.sort(key=lambda item: item[1])
        logger.info(f'Ticket stars: {[item[0].name.lower() for item in tickets]}')
        return tickets

    def read_star_row(self, x: int):
        """读取星标行：6 格，橙色点亮数量即星级
        :return: BossType 或 None（识别失败）
        """
        image = getattr(self.device, 'image', None)
        if image is None:
            return None
        y, w, h = 362, 64, 25  # 弹窗位置固定，实测 362~387 为星标行
        if x < 0 or x + w > image.shape[1]:
            return None
        crop = image[y:y + h, x:x + w]
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)  # device.image 是 RGB
        mask = cv2.inRange(hsv, (2, 100, 100), (32, 255, 255))  # 橙色点亮格
        seg = w / 6
        lit = 0
        for i in range(6):
            ratio = (mask[:, int(round(i * seg)):int(round((i + 1) * seg))] > 0).mean()
            if ratio > 0.22:  # 灰格实测 ≤0.21，橙格实测 ≥0.25
                lit += 1
            else:
                break
        if 1 <= lit <= 6:
            return BossType(lit)
        return None

    def read_star_by_icon(self, x1: int, x2: int):
        """用门票模板的底部星标条在单张票卡范围内匹配，兜底识别星级
        :return: BossType 或 None（匹配不明确时返回 None）
        """
        # [AI-2026-11-12] 整张票卡一二星同款无法区分，但底部星标条（橙点数）差异明显：
        # 实测正确星级 0.997+，次高 0.95，故只在单卡范围内比最高分，避免跨卡误匹配
        image = getattr(self.device, 'image', None)
        if image is None:
            return None
        y1, y2 = 348, 402  # 搜索窗口放宽，容忍弹窗位置上下偏移
        x1, x2 = max(0, int(x1)), min(image.shape[1], int(x2))
        region = image[y1:y2, x1:x2]
        if region.size == 0:
            return None
        ticket_icons = {
            BossType.ONE_STAR: self.I_MD_ONE_STAR,
            BossType.TWO_STARS: self.I_MD_TWO_STARS,
            BossType.THREE_STARS: self.I_MD_THREE_STARS,
            BossType.FOUR_STARS: self.I_MD_FOUR_STARS,
            BossType.FIVE_STARS: self.I_MD_FIVE_STARS,
            BossType.SIX_STARS: self.I_MD_SIX_STARS,
        }
        scores = {}
        for boss_type, rule in ticket_icons.items():
            # device.image 与 RuleImage.image 都是 RGB，直接使用，切勿再做通道转换
            band = rule.image[-22:, :]
            if region.shape[0] < band.shape[0] or region.shape[1] < band.shape[1]:
                continue
            result = cv2.matchTemplate(region, band, cv2.TM_CCOEFF_NORMED)
            scores[boss_type] = cv2.minMaxLoc(result)[1]
        if not scores:
            return None
        ranked = sorted(scores.items(), key=lambda item: -item[1])
        best_type, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_score < 0.9 or best_score - second_score < 0.02:  # 分数太低或与次高太近，视为不确定
            return None
        return best_type

    def check_and_switch_powerful(self) -> bool:
        """检查并切换是否开启强力"""
        logger.hr('process power fire')
        if not self.appear(self.I_MD_FIRE):
            self.goto_page(ipages.page_meta_demon_boss)
            self.screenshot()
            if not self.appear(self.I_MD_FIRE):  # 处于boss界面但是没有挑战按钮
                return False
        powerful_list = self.conf.meta_demon_config.powerful_list_v
        # 当前boss类型未知或不在强力列表中则使用普通追击
        if self.cur_boss_type is None or self.cur_boss_type not in powerful_list:
            self.ui_click(self.I_MD_ENABLE_POWERFUL_FIRE, self.I_MD_DISABLE_POWERFUL_FIRE, interval=0.6)
            return True
        # 已知类型且在强力列表中则开启强力追击
        self.ui_click(self.I_MD_DISABLE_POWERFUL_FIRE, self.I_MD_ENABLE_POWERFUL_FIRE, interval=0.6)
        return True

    def check_and_switch_soul(self) -> bool:
        """切换御魂"""
        if self.State.switch_soul_done:
            return True
        logger.hr('Switch soul')
        self.State.switch_soul_done = True
        self.goto_page(ipages.page_shikigami_records)
        if self.conf.switch_soul.switch_once:  # 只切换一次则将配置的鬼王御魂全部切换
            for boss_type in self.conf.meta_demon_config.fire_sequence_v:
                switch_type, (group, team) = self.conf.switch_soul.get_switch_by_enum(boss_type)
                if switch_type is None:
                    continue
                if switch_type == 'int':
                    self.run_switch_soul((group, team))
                if switch_type == 'str':
                    self.run_switch_soul_by_name(group, team)
            self.goto_page(ipages.page_meta_demon_boss)
            return True
        # 当前鬼王类型未知, 无法切换御魂
        if self.cur_boss_type is None:
            logger.error(f'cur_boss_type is None')
            self.goto_page(ipages.page_meta_demon_boss)
            return False
        switch_type, (group, team) = self.conf.switch_soul.get_switch_by_enum(self.cur_boss_type)
        if switch_type is None:
            logger.error(f'Switch soul format is invalid on {self.cur_boss_type.name.lower()}')
            self.goto_page(ipages.page_meta_demon_boss)
            return False
        if switch_type == 'int':
            self.run_switch_soul((group, team))
        if switch_type == 'str':
            self.run_switch_soul_by_name(group, team)
        self.goto_page(ipages.page_meta_demon_boss)
        return True

    def finish_task(self):
        self.goto_page(ipages.page_main)
        raise TaskEnd


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
