from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import (
    Page,
    page_act_list,
    page_battle,
    page_battle_result,
    page_main,
    page_reward,
    page_shikigami_records,
    random_click,
    reward_random_click,
)
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.MetaDemon.assets import MetaDemonAssets

# [AI-2026-11-11] 新 UI：活动总览默认选中当期版本活动，直接点「前往活动」进入活动主页
page_md_activity = Page(MetaDemonAssets.I_MD_ACT_ENTER)
page_act_list.connect(page_md_activity, GameUiAssets.I_ACT_LIST_GOTO_ACT, key="page_act_list->page_md_activity")
page_md_activity.connect(page_act_list, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_md_activity->page_act_list")

# [AI-2026-11-11] 新 UI 中超鬼王主界面与鬼王页已合并为一页，入口改为活动主页右侧「祸王降临」竖条
page_meta_demon_boss = Page(MetaDemonAssets.I_CHECK_BOSS_PAGE)
page_meta_demon_boss.add_enter_success_hooks(MetaDemonAssets.I_MD_GET_YESTERDAY_REWARD, MetaDemonAssets.I_MD_CLOSE_POPUP)
page_md_activity.connect(page_meta_demon_boss, MetaDemonAssets.I_MD_ACT_ENTER, key="page_md_activity->page_meta_demon_boss")
page_meta_demon_boss.connect(page_md_activity, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_meta_demon_boss->page_md_activity")

page_reward.connect(
    page_meta_demon_boss,
    reward_random_click(),
    key="page_reward->page_meta_demon_boss",
)
page_battle_result.connect(page_meta_demon_boss, random_click(), key="page_battle_result->page_meta_demon_boss")
