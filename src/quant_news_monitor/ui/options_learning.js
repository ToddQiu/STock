(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.STockOptionsLearning = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';

  const strategies = [
    {
      id: 'long_call', name: '买入 Call', level: '基础', outlook: '看涨',
      summary: '支付权利金，取得按行权价买入标的的权利。先练习“方向看对，仍可能亏钱”。',
      obligation: '买方可以平仓或放弃权利；真实账户持有实值期权到期时，还须处理自动行权及购股资金。',
      profit: '到期股价超过“行权价 + 每股权利金”才开始盈利，未计费用；理论上涨收益无上限。',
      risk: '期权本身最多损失付出的权利金及费用。涨得太慢、涨幅不足或隐含波动率下降，都可能使期权亏损。',
      example: '假设买入行权价 100 的 Call，每股付 5 美元。一张付 500 美元；到期股价 103 时仍亏 200 美元，股价 110 时赚 500 美元，均忽略费用。',
      steps: ['先看付出的总权利金及最大亏损。', '保持股价不变，缩短剩余天数，观察估值。', '再提高股价，对比到期前估值与到期损益。']
    },
    {
      id: 'long_put', name: '买入 Put', level: '基础', outlook: '看跌',
      summary: '支付权利金，取得按行权价卖出标的的权利。用于练习看跌时的收益与成本。',
      obligation: '买方没有必须行权的义务。真实账户行权涉及交付股票，未持股时可能形成空头，应提前核对券商规则。',
      profit: '到期股价低于“行权价 − 每股权利金”才开始盈利，未计费用。股价最低为零，因此收益有上限。',
      risk: '期权本身最多损失付出的权利金及费用。到期前下跌不够快，仍可能输给时间价值减少。',
      example: '假设买入行权价 100 的 Put，每股付 4 美元。到期股价 90 时，一张盈利 600 美元；股价 100 时损失 400 美元，均忽略费用。',
      steps: ['比较行权价与到期盈亏平衡点。', '让股价分别下跌、横盘、上涨。', '在同一股价下改变剩余天数和 IV，观察差别。']
    },
    {
      id: 'cash_put', name: '现金担保卖 Put', level: '基础', outlook: '中性偏多 · 愿意接货',
      summary: '收取权利金，同时准备足额现金，承担被指派后按行权价买入股票的义务。',
      obligation: '标准合约每张对应 100 股。卖一张行权价 60 的 Put，本区预留 6,000 美元接货资金，不能把它再次用于开仓。',
      profit: '这笔 Put 的最大收益是收到的权利金减费用；到期盈亏平衡点为行权价减每股净权利金。',
      risk: '标的大跌时仍要按约定价格接货，损失可能远大于权利金。现金担保避免借款，却不消除下跌风险。',
      example: '假设行权价 60、每股收 2 美元。一张收 200 美元；到期股价跌到 40，期权损益为 −1,800 美元，忽略费用。接货后股票仍会涨跌。',
      steps: ['确认接货总金额，不只看收到的权利金。', '模拟跌穿行权价，比较平仓、展期和到期接货。', '接货后观察股票浮盈亏，再决定是否练习备兑 Call。']
    },
    {
      id: 'covered_call', name: '备兑 Call', level: '基础', outlook: '中性偏多 · 接受卖出股票',
      summary: '持有股票并卖出对应数量的 Call，以让出部分上涨空间换取权利金。',
      obligation: '每张标准 Call 需要 100 股覆盖；被指派时按行权价交付股票。没有足额股票就是另一种风险结构。',
      profit: '被覆盖部分的到期上涨收益封顶。以买股同时卖 Call 为例，最大收益为“行权价 − 买股价 + 每股权利金”乘股数，未计费用。',
      risk: '权利金只缓冲少量下跌。股价大涨时股票可能被卖出；股价大跌时，主要股票风险仍然存在。',
      example: '假设以 100 买入 100 股，卖出行权价 110 的 Call，每股收 3 美元。到期股价为 130 时，这组持仓盈利仍为 1,300 美元，忽略费用。',
      steps: ['先确定一个能够接受的股票卖出价。', '比较小涨、大涨、大跌三种到期结果。', '平仓 Call 后核对股票是否还在，避免把平期权当成卖股票。']
    },
    {
      id: 'bull_call_spread', name: '牛市 Call 价差', level: '进阶', outlook: '温和看涨',
      summary: '买入较低行权价的 Call，同时卖出相同到期日、相同数量的较高行权价 Call。',
      obligation: '两条腿共同构成价差。真实美式短腿可能提前被指派，不能把长腿存在理解为一定自动抵消交割。',
      profit: '到期最大收益为行权价之差减每股净支出，再乘合约股数；上涨超过高行权价后收益不再增加。',
      risk: '完整持有并按到期价值结算时，最大损失为净支出及费用。只平掉一条腿会改变风险。',
      example: '假设买 100 Call、卖 110 Call，净付每股 4 美元。一组到期最大亏损 400 美元、最大收益 600 美元、盈亏平衡点 104，均忽略费用。',
      steps: ['确认两条腿同一到期日、数量相同。', '先比较净支出和行权价间距。', '将到期股价移到两个行权价之间，再移到高行权价之上。']
    },
    {
      id: 'bear_put_spread', name: '熊市 Put 价差', level: '进阶', outlook: '温和看跌',
      summary: '买入较高行权价的 Put，同时卖出相同到期日、相同数量的较低行权价 Put。',
      obligation: '卖出的 Put 带有接货义务；真实交易需管理两条腿的提前指派、成交差异及到期处理。',
      profit: '到期最大收益为行权价之差减每股净支出，再乘合约股数；跌至低行权价以下后收益封顶。',
      risk: '完整持有并按到期价值结算时，最大损失为净支出及费用。下跌幅度不足，也可能亏损。',
      example: '假设买 100 Put、卖 90 Put，净付每股 3 美元。一组到期最大亏损 300 美元、最大收益 700 美元、盈亏平衡点 97，均忽略费用。',
      steps: ['确认买入腿的行权价高于卖出腿。', '对比单独买 Put 与价差的成本和收益上限。', '模拟轻微下跌和大幅下跌，观察收益何时封顶。']
    },
    {
      id: 'protective_put', name: '保护性 Put', level: '基础', outlook: '持股看涨 · 限定保护期内下跌',
      summary: '持有股票并买入对应数量的 Put，用确定的权利金支出购买一段时间的下跌保护。',
      obligation: 'Put 买方取得卖出权利。保护只覆盖对应股数和有效期；到期后继续持股，需要重新评估风险。',
      profit: '保留股票的上涨空间，但盈利要先覆盖 Put 的购买成本及费用。',
      risk: '保护有成本，也有期限。以同时买股和 Put 为例，到期最大损失为“买股价 + 每股权利金 − 行权价”乘股数，另加费用。',
      example: '假设以 100 买入 100 股，并以每股 3 美元买入行权价 95 的 Put。到期股价即使跌至 70，组合损失仍为 800 美元，忽略费用。',
      steps: ['先看没有 Put 时股票下跌会损失多少。', '加入 Put，比较保护下限与权利金支出。', '到期结算后核对剩余股票，理解保护不会永久延续。']
    },
    {
      id: 'long_straddle', name: '买入跨式', level: '进阶', outlook: '预期大幅波动 · 方向未定',
      summary: '同时买入相同行权价、相同到期日的 Call 和 Put。需要足够大的价格变化补偿两份权利金。',
      obligation: '两条腿都是买方权利。真实到期可能出现一条腿自动行权，不能忽视交割资金和股票头寸。',
      profit: '到期有上下两个盈亏平衡点：行权价加、减每股总权利金。上涨收益理论无上限，下跌收益受股价零下限约束。',
      risk: '到期停在行权价附近可能损失全部权利金。重大事件结束后 IV 下跌，即使股价有波动也可能亏损。',
      example: '假设行权价 100，两条腿合计每股付 8 美元。到期盈亏平衡点为 92 和 108；股价 105 时一组仍亏 300 美元，忽略费用。',
      steps: ['先计算两条腿合计成本。', '模拟价格向两个方向移动，找到两个盈亏平衡点。', '保持股价不变，降低 IV，观察到期前估值下降。']
    }
  ];

  const lessons = [
    {
      id: 'contract', title: '先认清你买卖的约定',
      lead: 'Call 和 Put 是两种权利；买入和卖出决定你持有权利，还是承担义务。',
      paragraphs: [
        '买 Call：付费获得按行权价买入的权利。卖 Call：收费并承担被指派时按行权价卖出的义务。买 Put：付费获得按行权价卖出的权利。卖 Put：收费并承担被指派时按行权价买入的义务。',
        '标准美国股票期权通常一张对应 100 股，报价通常按每股表示。每股 2 美元、1 张合约，权利金就是 200 美元。拆股等公司行动后的调整合约可能不同，本区只模拟 100 股标准合约。',
        '先读标的、Call/Put、行权价、到期日和张数，再看价格。买入后卖出同一合约可以平仓；卖出后买回同一合约可以平仓。平仓与行权不是一回事。'
      ],
      quiz: {
        question: '卖出一张标准、行权价 60 美元的 Put，被指派时需要做什么？',
        choices: ['按 60 美元买入 100 股', '按当时市场价买入 1 股', '只退回已经收到的权利金'],
        answer: 0,
        explanation: '卖 Put 承担买入义务。标准合约对应 100 股，因此接货金额为 6,000 美元，权利金不会取消这一义务。'
      },
      strategy: 'cash_put'
    },
    {
      id: 'pricing', title: '看对方向，为什么还会亏？',
      lead: '到期前的期权价格，除了股价，还受剩余时间、隐含波动率等因素影响。',
      paragraphs: [
        '到期时，Call 每股价值为股价超过行权价的部分，Put 每股价值为行权价超过股价的部分，最低为零。持有期权有内在价值，不代表已经覆盖最初付出的权利金。',
        '到期前通常还有时间价值。先保持股价不变，只减少剩余天数；再保持时间不变，只调整 IV。分别观察，才知道某次盈亏来自哪一个因素。',
        'Delta 描述其他条件不变时，股价小幅变动对期权价格的近似影响。它会随行情变化，不能直接当成你的盈利概率；到期实值也未必盈利。',
        '本区使用无股息的欧式 Black–Scholes 理论估值，IV 是你输入的情景假设。真实美国股票/ETF 期权通常允许提前行权，市场价格也受买卖价差、流动性和股息影响；这里的模型价格不保证能够成交。'
      ],
      quiz: {
        question: '假设买入 100 Call，每股付 5 美元，到期股价是 103。一张的损益是多少（忽略费用）？',
        choices: ['盈利 300 美元', '亏损 200 美元', '刚好回本'],
        answer: 1,
        explanation: '到期内在价值为每股 3 美元，减去每股 5 美元成本，再乘 100 股，结果是 −200 美元。'
      },
      strategy: 'long_call'
    },
    {
      id: 'cash', title: '收到权利金，不等于赚到利润',
      lead: '现金、可用资金和净值回答的是三个不同的问题。',
      paragraphs: [
        '卖出期权时，现金增加，同时出现期权负债。忽略费用、以相同价格估值，开仓瞬间净值不变。之后回购更便宜，才可能实现利润；回购更贵，就会实现亏损。',
        '净值等于现金加股票市值，再加有符号的期权估值：买方期权为资产，卖方期权为负债。预留接货现金仍属于你的资产，但已经不能自由用于另一笔交易。',
        '现金担保卖 Put 按行权价 × 100 × 张数准备接货资金。本区不借款开仓。所谓“50% 仓位”必须先说清分母和占用口径，不能只用收到的权利金或券商最低保证金衡量风险。'
      ],
      quiz: {
        question: '卖 Put 收到 200 美元，开仓时该期权负债也是 200 美元。忽略费用，净值如何变化？',
        choices: ['增加 200 美元', '减少 200 美元', '保持不变'],
        answer: 2,
        explanation: '现金 +200 与负债 −200 相抵。把权利金收入全部计为盈利，会漏掉仍需承担的合约义务。'
      },
      strategy: 'cash_put'
    },
    {
      id: 'roll', title: '展期、接货、备兑各改变了什么？',
      lead: '连续操作要看整本账，不能每开一张新合约就忘记上一张。',
      paragraphs: [
        '展期是先平旧仓，再开新仓。例如最初收 200，花 800 买回旧 Put，再卖新 Put 收 900：本次展期净收到 100，但旧仓已经亏了 600，新仓还带着新的义务。所有数字均为假设，忽略费用。',
        'Put 接货后，你从承担买入义务变成实际持股。再卖备兑 Call 会限制对应股票的上涨收益；权利金可以缓冲一部分损失，却不保证股价会反弹或账户会回本。',
        '轮动策略通常是现金担保卖 Put → 接货 → 持股卖 Call → 股票交付后再次评估。每次都要检查仍愿不愿意承担该标的风险；展期、补仓、等待都不是必须执行的步骤。',
        '本区卖 Put 与备兑 Call 在到期时模拟股票交付，其他期权按到期内在价值进行教学结算。真实美式期权可能提前被指派；价差的两条腿也不保证同步处理，实盘不能照搬这里的简化结算。'
      ],
      quiz: {
        question: '展期时净收到额外权利金，是否说明旧合约的亏损已经消失？',
        choices: ['是，新权利金会自动冲销全部亏损', '否，要保留旧仓损益，并继续计算新仓负债', '只要新行权价更低就算回本'],
        answer: 1,
        explanation: '净现金流和利润不同。展期改变后续的权利、义务及期限，旧仓平仓损益仍然属于账户累计结果。'
      },
      strategy: 'covered_call'
    },
    {
      id: 'risk', title: '先定代价，再选择策略',
      lead: '不同策略在方向、成本、保护和收益上限之间取舍。',
      paragraphs: [
        '买 Call/Put 用权利金承担方向判断的成本；借助同到期价差，可以同时限定理论到期收益和损失。保护性 Put 则为已有股票购买有期限的下跌保护。',
        '买跨式同时买 Call 和 Put，需要足够大的价格变化覆盖两份成本。预期会发生大事件，不等于期权便宜；事件后 IV 下降也会影响到期前价格。',
        '练习时先写下：愿意亏多少、是否愿意接货或交股、何时重新评估。复盘同时看净值、最大损失和资金占用，不能只比较权利金多少或盈利次数。'
      ],
      quiz: {
        question: '股价可能大幅波动，但你不确定方向。买跨式之后，哪种说法正确？',
        choices: ['股价只要动就能赚钱', '两份权利金相互抵消，不需要成本', '波动幅度仍需覆盖总权利金，且到期前还受 IV 和时间影响'],
        answer: 2,
        explanation: '买跨式是支付两份权利金。价格变动不足时仍会亏损，不能把“不猜方向”理解成“不承担风险”。'
      },
      strategy: 'long_straddle'
    },
    {
      id: 'soxl', title: '进阶：SOXL 为什么要单独理解？',
      lead: '先理解期权，再理解期权所挂钩的三倍杠杆 ETF。',
      paragraphs: [
        'SOXL 的目标是费用前实现基准指数每日收益的三倍，不保证长期累计收益是三倍。每天重新计算的杠杆，使长期结果依赖价格走过的路径。',
        '理想化例子：指数从 100 跌 10% 到 90，再涨约 11.11% 回到 100。准确实现每日三倍的基金会从 100 到 70，再到约 93.33，忽略费用和跟踪误差。持续趋势下复利也可能放大收益，并非每天固定扣除“损耗费”。',
        '在这样的标的上卖 Put、展期或卖 Call，并没有消除标的的路径风险。高权利金也不等于便宜的风险；连续下跌、接货后再跌、反弹被 Call 封顶，都应该分别模拟。',
        '这里的 SOXL 案例是进阶情景练习，不是实时报价或历史收益证明。先用普通假设标的理解资金和合约，再尝试杠杆案例。'
      ],
      quiz: {
        question: '半导体指数经过多日涨跌回到原点，SOXL 是否一定也回到原点？',
        choices: ['一定，因为它只是乘以三', '不一定，累计结果取决于每日涨跌路径及费用等因素', '一定亏掉固定 7%'],
        answer: 1,
        explanation: '三倍针对每日收益。6.67% 的损失只对应本课的两日假设路径，不是所有期间都会发生的固定损耗。'
      },
      strategy: 'cash_put'
    }
  ];

  const glossary = [
    {term: '合约乘数', meaning: '把每股报价换成一张合约金额。本区标准合约为 100 股，调整合约可能不同。', example: '每股 2 美元 × 100 × 2 张 = 400 美元。'},
    {term: '行权价', meaning: '合约约定买入或卖出标的的价格，不是到期后的市场价格。', example: '60 Put 被指派时按每股 60 买入，哪怕市场只有 40。'},
    {term: '到期日', meaning: '合约权利的期限。美式期权还可能提前行权，本区只模拟到期交割。', example: '剩余 30 天与剩余 1 天的同一行权价合约，价格可能明显不同。'},
    {term: '权利金', meaning: '买方支付、卖方收取的期权价格；收到现金不等于已经赚到利润。', example: '卖出收 200，之后花 350 买回，忽略费用亏 150。'},
    {term: '内在价值', meaning: 'Call 为股价减行权价，Put 为行权价减股价，结果最低为零。', example: '股价 108、行权价 100 的 Call，内在价值每股 8。'},
    {term: '时间价值', meaning: '通常指权利金超过内在价值的部分，与剩余期限、波动等因素有关。欧式深度实值 Put 也会受利率影响，不能把所有价差都理解为正的时间价值。', example: 'Call 报价 10、内在价值 8，通常说时间价值为 2。'},
    {term: '隐含波动率 IV', meaning: '从市场期权价格反推的波动率参数；本区把它作为手动输入的情景假设，不代表未来一定实现。', example: '股价和期限不变时，提高 IV 通常会提高单独买入的 Call/Put 理论价格。'},
    {term: 'Delta', meaning: '其他因素不变时，股价小幅变化对期权每股价格的局部敏感度；不是固定倍率，也不是盈利概率。', example: 'Call 的 Delta 为 0.5 时，股价小涨 1 美元，期权每股价格近似涨 0.5 美元。'},
    {term: '指派 / 接货', meaning: '卖方收到履行合约义务的分配。卖 Put 买入股票，卖 Call 交付股票。', example: '一张标准 Put 被指派，通常需要买入 100 股。'},
    {term: '展期 Roll', meaning: '平掉现有期权，再建立另一到期日或行权价的期权仓位；两笔账都要保留。', example: '旧仓已实现亏损 600，即使新仓收到 900，也不能立刻记为盈利 300。'}
  ];

  const sources = [
    {title: 'OIC · 期权基础', url: 'https://www.optionseducation.org/optionsoverview/options-basics'},
    {title: 'OIC · 买入 Call', url: 'https://www.optionseducation.org/strategies/all-strategies/long-call'},
    {title: 'OIC · 买入 Put', url: 'https://www.optionseducation.org/strategies/all-strategies/long-put'},
    {title: 'OIC · 现金担保卖 Put', url: 'https://www.optionseducation.org/strategies/all-strategies/cash-secured-put'},
    {title: 'OIC · 备兑 Call', url: 'https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write'},
    {title: 'OIC · 牛市 Call 价差', url: 'https://www.optionseducation.org/strategies/all-strategies/bull-call-spread-debit-call-spread'},
    {title: 'OIC · 熊市 Put 价差', url: 'https://www.optionseducation.org/strategies/all-strategies/bear-put-spread'},
    {title: 'OIC · 保护性 Put', url: 'https://www.optionseducation.org/strategies/all-strategies/protective-put-married-put'},
    {title: 'OIC · 买入跨式', url: 'https://www.optionseducation.org/strategies/all-strategies/long-straddle'},
    {title: 'OIC · 理论定价的用途与限制', url: 'https://www.optionseducation.org/advancedconcepts/black-scholes-formula'},
    {title: 'OIC · Delta', url: 'https://www.optionseducation.org/advancedconcepts/delta'},
    {title: 'OIC · 展期如何改变风险与收益', url: 'https://www.optionseducation.org/news/october-webinar-key-takeaways-rolling-options-and-repairing-stock-positions'},
    {title: 'Direxion · SOXL 每日三倍目标与风险', url: 'https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs'}
  ];

  return {strategies, lessons, glossary, sources};
});
