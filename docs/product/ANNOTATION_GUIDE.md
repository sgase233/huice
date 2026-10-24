# 政策条款标注规范 ANNOTATION_GUIDE
## 目的
用于双人独立标注政策条件，产出 criteria_review.csv，供M1后端导入系统做规则判断。

## 字段说明（criteria_review.csv）
1. criterionId：条件唯一编号，crit‑01、crit‑02依次编号
2. policyId：对应source.csv里面的policy_id（P001/P002/P003…）
3. expressionExpect：用文字描述这条条件的业务逻辑，不用写代码AST
4. sourceSpan：记录政策原文位置，例如：正文第二条第3款
5. sourceText：复制政策原文一小段文字
6. reviewStatus：pending=待复核；reviewed=双人复核完成

## 双人标注流程
1. 两个人独立对【政策+企业】组合做判断
2. 两人结果一样 → reviewStatus标记reviewed
3. 两人结果不一样 → 发起仲裁，核对官方原文，确定最终预期结果

## 重要约束
1. 全部以**政府官方原文**为准，不能使用新闻、公众号解读。
2. 未知事实填`null`，绝对不能填0。
3. 四态枚举：
- satisfied：满足申报条件
- not_satisfied：不满足申报条件
- insufficient_evidence：证据不足，缺少企业信息
- conflict：条款冲突，需要人工复核
