# 本地动作目录

动作范围来自 `mate_engine_motions` 中的 128 个 VRMA 文件。自动待机使用原版预制体的 11 项范围，并只保留本地实际存在的动作。其它动作登记为可用资源，不会混入自动待机。

## 自动待机顺序

| 顺序 | 动作 | 播放速度 |
| --- | --- | --- |
| 1 | PET_IDLE/PET_IDLE | 0.3 × |
| 2 | UPDATE_2/PET_IDLE_UPDATE2_01 | 1 × |
| 3 | UPDATE_2/PET_IDLE_UPDATE2_02 | 1 × |
| 4 | UPDATE_2/PET_IDLE_UPDATE2_03 | 1 × |
| 5 | UPDATE_2/PET_IDLE_UPDATE2_04 | 1 × |
| 6 | PET_IDLE/PET_IDLE_2 | 1 × |
| 7 | PET_IDLE/PET_IDLE_14 | 0.4 × |
| 8 | PET_IDLE/PET_IDLE_4 | 1 × |
| 9 | PET_IDLE/PET_IDLE_3 | 1 × |
| 10 | PET_IDLE/PET_IDLE_5 | 1 × |
| 11 | PET_IDLE/PET_IDLE_15 | 0.4 × |

## 全部可用资源

速度取自原版对应的待机混合树或状态；同一素材在不同状态有不同配置时，完整引用保存在 `web/vrm/mate-motion-profile.json`。没有控制器引用的素材按 1 倍作为手动预览默认值，不冒充原版状态配置。

| 文件 | 时长（秒） | 控制器引用 |
| --- | --- | --- |
| FACE_LAYER/FACE_DRAG.vrma | 10.033334 | 未引用 |
| FACE_LAYER/FACE_HAIR_STROKE.vrma | 10.033334 | 未引用 |
| FACE_LAYER/FACE_IDLE_1.vrma | 10.033334 | Faceloop：1 × |
| FACE_LAYER/FACE_INTIME.vrma | 10.033334 | 未引用 |
| FACE_LAYER/FACE_RESET.vrma | 1.983333 | 未引用 |
| FACE_LAYER/PET_HAPPY.vrma | 1 | 未引用 |
| PET_BIG_SCREEN/BIG_SCREEN_01.vrma | 13.333334 | BigScreenBlend：0.5 ×；BigScreenBlend：0.5 ×；Alarm：1 × |
| PET_BIG_SCREEN/SCREEN_SAVER_01.vrma | 13.333334 | BigScreenBlend：0.5 ×；Animation 1：1 × |
| PET_BIG_SCREEN/SCREEN_SAVER_02.vrma | 3.733334 | Animation 2：1 × |
| PET_BIG_SCREEN/SCREEN_SAVER_03.vrma | 13.333334 | Animation 3：1 × |
| PET_BIG_SCREEN/SCREEN_SAVER_04.vrma | 16.733353 | Screen Saver：1 × |
| PET_DANCING/ME_02/HUSBANDO/HUS_DANCE_01.vrma | 15.966667 | 未引用 |
| PET_DANCING/ME_02/HUSBANDO/HUS_DANCE_02.vrma | 26.166668 | 未引用 |
| PET_DANCING/ME_02/HUSBANDO/HUS_DANCE_03.vrma | 12.35 | 未引用 |
| PET_DANCING/ME_02/HUSBANDO/HUS_DANCE_04.vrma | 6.333333 | 未引用 |
| PET_DANCING/PET_DANCING_10.vrma | 21.783335 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_11.vrma | 27.350002 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_12.vrma | 6.333333 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_13.vrma | 19.633335 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_2.vrma | 1.333333 | DanceIndex：0.6 × |
| PET_DANCING/PET_DANCING_3.vrma | 3.849976 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_4.vrma | 2.266668 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_5.vrma | 1.700012 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_6.vrma | 2.416668 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING_7.vrma | 5.016663 | DanceIndex：1.25 × |
| PET_DANCING/PET_DANCING_8.vrma | 2.349998 | DanceIndex：1.25 × |
| PET_DANCING/PET_DANCING_9.vrma | 4.483334 | DanceIndex：1 × |
| PET_DANCING/PET_DANCING.vrma | 14.216667 | DanceIndex：1 ×；DanceIndex：1 × |
| PET_HIDING/PET_HIDE 1.vrma | 13.983368 | 未引用 |
| PET_HIDING/PET_HIDE_SHOW_LOOP_LEFT.vrma | 8 | 未引用 |
| PET_HIDING/PET_HIDE_SHOW_LOOP_RIGHT.vrma | 8 | 未引用 |
| PET_HIDING/PET_HIDE.vrma | 13.983368 | 未引用 |
| PET_HIDING/TEST_HIDE_LEFT.vrma | 1 | 未引用 |
| PET_HIDING/TEST_HIDE_RIGHT.vrma | 1 | 未引用 |
| PET_IDLE/CUSTOM_DANCE.vrma | 2.450012 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_DRAG.vrma | 4.5 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE01.vrma | 8.866667 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE02.vrma | 10.666667 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE03.vrma | 4.266667 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE04.vrma | 9.916667 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE05.vrma | 4.466667 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE06.vrma | 6.316667 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE07.vrma | 3.183333 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE08.vrma | 10.55 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_IDLE09.vrma | 3.583333 | 未引用 |
| PET_IDLE/ME_02/HUSBANDO/HUS_SITTING.vrma | 10.866667 | 未引用 |
| PET_IDLE/ME_02/PET_IDLE_16.vrma | 13.475 | 未引用 |
| PET_IDLE/ME_02/PET_IDLE_17.vrma | 14.700001 | 未引用 |
| PET_IDLE/ME_02/PET_IDLE_19.vrma | 14.575001 | 未引用 |
| PET_IDLE/ME_02/PET_IDLE_20.vrma | 12.725 | 未引用 |
| PET_IDLE/ME_02/PET_IDLE_21.vrma | 17.016668 | 未引用 |
| PET_IDLE/ME_02/PET_IDLE_22.vrma | 14.166667 | 未引用 |
| PET_IDLE/PET_IDLE 1.vrma | 2.450012 | 未引用 |
| PET_IDLE/PET_IDLE_10.vrma | 11.000001 | IdleIndex：0.85 × |
| PET_IDLE/PET_IDLE_11.vrma | 4.066667 | IdleIndex：0.4 × |
| PET_IDLE/PET_IDLE_12.vrma | 3.466667 | IdleIndex：0.4 × |
| PET_IDLE/PET_IDLE_13.vrma | 4.133334 | IdleIndex：0.4 × |
| PET_IDLE/PET_IDLE_14.vrma | 4.366667 | IdleIndex：0.4 × |
| PET_IDLE/PET_IDLE_15.vrma | 4.766667 | IdleIndex：0.4 × |
| PET_IDLE/PET_IDLE_2.vrma | 13.333334 | IdleIndex：1 × |
| PET_IDLE/PET_IDLE_3.vrma | 3.733334 | IdleIndex：1 × |
| PET_IDLE/PET_IDLE_4.vrma | 13.333334 | IdleIndex：1 × |
| PET_IDLE/PET_IDLE_5.vrma | 9.500001 | IdleIndex：1 × |
| PET_IDLE/PET_IDLE_6.vrma | 5.666667 | IdleIndex：1 × |
| PET_IDLE/PET_IDLE_7.vrma | 9.333334 | IdleIndex：1 × |
| PET_IDLE/PET_IDLE_8.vrma | 6 | IdleIndex：1 × |
| PET_IDLE/PET_IDLE_9.vrma | 10.000001 | IdleIndex：0.75 × |
| PET_IDLE/PET_IDLE_NON_1.vrma | 7 | 未引用 |
| PET_IDLE/PET_IDLE.vrma | 2.450012 | IdleIndex：0.3 ×；IdleIndex：0.3 × |
| PET_INTRO/PET_INTRO_END.vrma | 1.533334 | 未引用 |
| PET_INTRO/PET_INTRO_LOOP.vrma | 13.333334 | 未引用 |
| PET_INTRO/PET_INTRO_START.vrma | 2 | 未引用 |
| PET_INTRO/PET_INTRO.vrma | 10.18335 | Intro：1 × |
| PET_LOCOMOTION/PET_WALK_LEFT.vrma | 1.416667 | 未引用 |
| PET_LOCOMOTION/PET_WALK_RIGHT.vrma | 1.416667 | 未引用 |
| PET_LOCOMOTION/walk_cycle_sexy_01_light.vrma | 1.416667 | 未引用 |
| PET_LOCOMOTION/walk_cycle_sexy_02_light.vrma | 1.416667 | 未引用 |
| PET_LOCOMOTION/walk_cycle_sexy_02.vrma | 1.416667 | 未引用 |
| PET_LOCOMOTION/walk_cycle_sexy_03_light.vrma | 1.416667 | 未引用 |
| PET_LOCOMOTION/walk_cycle_sexy_03.vrma | 1.416667 | 未引用 |
| PET_MISC/FACE_SMILE 1.vrma | 4 | 未引用 |
| PET_MISC/FACE_SMILE.vrma | 4 | 未引用 |
| PET_MISC/HoverFace.vrma | 2.450012 | HoverFace：1 × |
| PET_MISC/HoverReaction.vrma | 2.450012 | HoverReaction：1 × |
| PET_MISC/PET_DRAGGING.vrma | 12.783334 | Drag：1 × |
| PET_MISC/PET_HAPPY.vrma | 6.55 | 未引用 |
| PET_MISC/PET_LAUGHING.vrma | 8.333334 | 未引用 |
| PET_MISC/PET_SHY_POINT.vrma | 11.000001 | 未引用 |
| PET_MISC/PLACE_HOLDER_ANIMATION.vrma | 3 | 未引用 |
| PET_POSE/PET_POSE_1.vrma | 6.8 | 未引用 |
| PET_POSE/PET_POSE_2.vrma | 6.8 | 未引用 |
| PET_POSE/PET_POSE_3.vrma | 5 | 未引用 |
| PET_POSE/PET_POSE_4.vrma | 6.8 | 未引用 |
| PET_SITTING/BETA_PET_WINDOW_LAY_2.vrma | 0.016667 | WindowSitIndex：2 × |
| PET_SITTING/BETA_PET_WINDOW_LAY_3.vrma | 0.033333 | WindowSitIndex：3 × |
| PET_SITTING/BETA_PET_WINDOW_LAY_4.vrma | 0.033333 | WindowSitIndex：1 × |
| PET_SITTING/BETA_PET_WINDOW_LAY_5.vrma | 0 | WindowSitIndex：1 × |
| PET_SITTING/BETA_PET_WINDOW_LAY_6.vrma | 0 | WindowSitIndex：1 × |
| PET_SITTING/BETA_PET_WINDOW_LAY_7.vrma | 0 | WindowSitIndex：1 × |
| PET_SITTING/BETA_PET_WINDOW_LAY.vrma | 19.958334 | WindowSitIndex：1 × |
| PET_SITTING/ME_02/PET_SIT_01.vrma | 3.166748 | 未引用 |
| PET_SITTING/ME_02/PET_SIT_02.vrma | 6.466675 | 未引用 |
| PET_SITTING/ME_02/PET_SIT_03.vrma | 6.466667 | 未引用 |
| PET_SITTING/ME_02/PET_SIT_04.vrma | 2 | 未引用 |
| PET_SITTING/ME_02/PET_SIT_05.vrma | 4 | 未引用 |
| PET_SITTING/ME_02/PET_SIT_06.vrma | 6 | 未引用 |
| PET_SITTING/ME_02/PET_SIT_07.vrma | 8 | 未引用 |
| PET_SITTING/ME_02/PET_SIT_SMOOTHIE_01.vrma | 6.35 | 未引用 |
| PET_SITTING/ME_02/sit 1.vrma | 2.316666 | 未引用 |
| PET_SITTING/ME_02/suwari1_copy.vrma | 6.35 | 未引用 |
| PET_SITTING/ME_02/suwari1.vrma | 3.166667 | 未引用 |
| PET_SITTING/ME_02/suwari3.vrma | 2.850098 | 未引用 |
| PET_SITTING/ME_02/WINDOW_LAY_10.vrma | 3.016663 | WindowSitIndex：1 × |
| PET_SITTING/ME_02/WINDOW_LAY_8.vrma | 2.399994 | 未引用 |
| PET_SITTING/ME_02/WINDOW_LAY_9.vrma | 2.050003 | WindowSitIndex：1 × |
| PET_SITTING/PET_SITTING_DEMO_2.vrma | 0 | 未引用 |
| PET_SITTING/PET_SITTING_DEMO.vrma | 0.016667 | 未引用 |
| PET_SITTING/sippose39.vrma | 0 | 未引用 |
| PET_SITTING/sipposeC06.vrma | 0 | 未引用 |
| PET_SLEEPING/PET_SLEEPING.vrma | 35.166668 | Sleeping：0.6 × |
| U_MOTIONS/AnkhaZone.vrma | 1 | 未引用 |
| U_MOTIONS/Float 2.vrma | 26.040001 | 未引用 |
| U_MOTIONS/Float v3.vrma | 45 | 未引用 |
| UPDATE_2/PET_IDLE_UPDATE2_01.vrma | 16.5 | BigScreenBlend：0.7 ×；IdleIndex：1 × |
| UPDATE_2/PET_IDLE_UPDATE2_02.vrma | 13.233337 | BigScreenBlend：0.7 ×；IdleIndex：1 × |
| UPDATE_2/PET_IDLE_UPDATE2_03.vrma | 11.416687 | BigScreenBlend：0.7 ×；IdleIndex：1 × |
| UPDATE_2/PET_IDLE_UPDATE2_04.vrma | 11.983368 | BigScreenBlend：0.7 ×；IdleIndex：1 × |
| UPDATE_2/PET_TALKING.vrma | 1 | PET_TALKING：1 ×；Talk：1 × |
