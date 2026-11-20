# This Python file uses the following encoding: utf-8
from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.Buy.buy import Buy
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.page import page_mall, page_mall_recommend
from tasks.TicketShop.config import TicketAccountInfo
from tasks.WeeklyPurchase.mall.navbar import MallNavbar


class ScriptTask(Buy, MallNavbar):
    """寄售券购买：逐个切小号，在寄售屋把本周剩余的兑换次数买满。

    判定全部依赖界面读数（AI-2026-11-20 实机确认）：
    - 「本周剩余数量」=0 → 已兑换；买 1~9 次商品仍在原位、数字递减，售罄才移到最后
    - 勾玉是顶栏第 3 个资源位
    """

    def run(self):
        conf = self.config.ticket_shop
        if not conf.sup_account_list:
            self._seed_from_find_jade(conf)

        all_success = True
        switched = False

        for info in conf.sup_account_list:
            if not info.is_valid():
                continue
            # [AI-2026-11-20] 轮到该号先清空状态，跑完再写，避免残留上一轮
            if info.ticket_status:
                info.ticket_status = ''
                self.config.save()

            if not SwitchAccount(self.config, self.device, info).switchAccount():
                logger.warning("寄售券：切换 %s-%s 失败，跳过该号", info.character, info.svr)
                info.ticket_status = '切换失败'
                self.config.save()
                all_success = False
                continue
            switched = True

            try:
                status, ok = self._exchange_ticket()
            except RequestHumanTakeover:
                raise
            except Exception as e:
                logger.error("寄售券：%s-%s 执行异常 %s", info.character, info.svr, e)
                status, ok = f'执行异常({type(e).__name__})', False

            info.ticket_status = status
            self.config.save()
            all_success = all_success and ok
            logger.info("寄售券：%s-%s -> %s", info.character, info.svr, status)

        if switched:
            self._switch_back()

        # 整轮全部「已兑换」才算成功（用户确认）
        self.next_run('TicketShop', success=all_success)
        raise TaskEnd('TicketShop')

    def _exchange_ticket(self):
        """在当前小号上交兑换，返回 (状态文案, 是否成功)。"""
        threshold = self.config.ticket_shop.ticket_shop_config.jade_threshold

        self.goto_page(page_mall_recommend, confirm_wait=2.5, accepted_pages=(page_mall,))
        self._enter_consignment()
        self.ui_click(self.I_CON_ENTER, self.I_CON_ENTER_CHECK)

        # [AI-2026-11-20] 先读数字再找商品：售罄后商品会挪到最后一格，
        # 用固定槽位等它会超时，但「本周剩余数量」标签的位置不变。
        self.screenshot()
        remain = self.O_CON_NUMBER.ocr(self.device.image)
        if not isinstance(remain, int):
            return '界面异常(读不到剩余数量)', False
        if remain == 0:
            logger.info("寄售券：本周剩余 0 次，已兑换")
            return '已兑换', True

        jade = self.mall_resource(3)
        if not isinstance(jade, int):
            return '界面异常(读不到勾玉)', False
        if jade < threshold:
            logger.warning("寄售券：勾玉 %s 低于门槛 %s，跳过", jade, threshold)
            return f'勾玉不足({jade})', False

        logger.info("寄售券：剩余 %s 次，勾玉 %s，开始兑换", remain, jade)
        result = self.buy_more(self.I_CON_TICKET)
        if result is False:
            # 弹窗里提示已达上限/勾玉不足都会走到这里，复核一次再定性
            self.screenshot()
            after = self.O_CON_NUMBER.ocr(self.device.image)
            if after == 0:
                return '已兑换', True
            return f'购买失败(剩余 {after})', False

        self.screenshot()
        after = self.O_CON_NUMBER.ocr(self.device.image)
        if after == 0:
            logger.info("寄售券：兑换完成，剩余 0 次")
            return '已兑换', True
        return f'购买失败(剩余 {after})', False

    def _seed_from_find_jade(self, conf):
        """首次运行：把小号列表从「寻找协作」复制一份，省得重填。"""
        source = self.config.find_jade.sup_account_list or []
        if not source:
            logger.warning("寄售券：寻找协作里没有小号，无法自动导入")
            return
        conf.sup_account_list = [TicketAccountInfo(**item.model_dump()) for item in source]
        conf.ticket_shop_config.sup_account_count = len(conf.sup_account_list)
        self.config.save()
        logger.info("寄售券：已从寻找协作导入 %s 个小号", len(conf.sup_account_list))

    def _switch_back(self):
        """按 return_account 切回；失败重试 2 次仍不成功则交人工。"""
        account = self.config.ticket_shop.return_account
        if account is None or not account.is_valid():
            return
        if not account.account:
            logger.warning('寄售券：return_account 已填角色名但没填账号，无法切回')
            return
        for attempt in range(1, 4):
            logger.info('寄售券：切回 %s-%s（第 %d 次）', account.character, account.svr, attempt)
            if SwitchAccount(self.config, self.device, account).switchAccount():
                logger.info('寄售券：切回成功 %s-%s', account.character, account.svr)
                return
            logger.warning('寄售券：切回失败（第 %d 次）%s-%s', attempt, account.character, account.svr)
        raise RequestHumanTakeover

    def next_run(self, task: str, success: bool = True):
        """按「成功/失败间隔」推算下一次：finish=True 表示以完成时间为基准。"""
        self.set_next_run(task, finish=True, success=success)
