# 文献适配基线与消融：小型验收

10组检查通过；仅解析/小型输入，未运行60例正式对比或参数校准。

- quadratic_power_closed_form_and_new_task_exclusion: PASS
- quadratic_energy_closed_form: PASS
- zero_penalty_same_milp_cross_solver: PASS
- integer_mode_arbitrage_guard: PASS
- same_infeasible_physical_model: PASS
- payoff_normalization_and_scalar_objective: PASS
- normalization_is_affine_not_clipped: PASS
- coordination_ablation_and_identical_physics: PASS
- plan_priority_ablation_closed_form: PASS
- invalid_parameter_rejection: PASS

SCIP保留所有原物理变量界、整数性和约束，逐行读取后核对导入；返回计划由原独立核验器检查。
平方项采用精确二次上图约束，没有分段线性近似或放松整数模式。
目标值从原始计划独立重算，平方功率/状态例另有手算值；零惩罚与HiGHS经济解交叉检查。
三种消融仅删去相应目标阶段，物理模型摘要和共同经济预算不变。
归一化收益表参照不是全局最坏界，零跨度采用正尺度，不裁剪归一化值。
这证明实现符合已声明的适配机制，不代表复现原论文全部市场、网络或滚动系统。
