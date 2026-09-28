#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
from collections import OrderedDict, namedtuple
import json
from pathlib import Path
import re
import sys


DESCRIPTION = (
    "Generate the judge's family 13 corpus for the IK tokenizer as mysqltest files. "
    "'cases' writes cases/*.test; 'cases --check' compares cases/ with a fresh generation; "
    "'check-recording' reads a recording of the corpus and checks every statement's errno line "
    "and the shape of every token list; 'diff-tokens' tells, for each statement two recordings "
    "print differently, whether only the order of the tokens changed. None of them talks to a server."
)

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "cases"
FILE_LIMIT = 60
SESSION = (
    "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci, ob_enable_plan_cache = 0, "
    "ob_query_timeout = 600000000, ob_trx_timeout = 600000000"
)
SMART = '[{"output": "all"}, {"additional_args": [{"ik_mode": "smart"}]}]'
MAX_WORD = '[{"output": "all"}, {"additional_args": [{"ik_mode": "max_word"}]}]'
MAX_WORD_DEFAULT = '[{"additional_args": [{"ik_mode": "max_word"}]}]'
MODES = (("smart", SMART), ("max_word", MAX_WORD))
ERRNO_LINE = re.compile(r"^errno (-?\d+)$")
CLIENT_ERRNO_FIRST = 2000
CLIENT_ERRNO_LAST = 2999
ROW_KINDS = ("rows_all", "rows_default")
JSON_KINDS = ("all", "default", "long") + ROW_KINDS

Statement = namedtuple("Statement", "sql errno kind extra")
Case = namedtuple("Case", "text statements")


class GeneratorError(Exception):
    pass


PROSE = (
    "今天上午，国家统计局发布了第三季度的经济数据。",
    "随着人工智能技术的快速发展，越来越多的企业开始使用大语言模型提升工作效率。",
    "春天来了，公园里的桃花和樱花竞相开放，吸引了许多市民前来踏青赏花。",
    "这家餐厅的招牌菜是红烧肉和清蒸鲈鱼，味道鲜美，价格也很实惠。",
    "数据库管理系统负责存储、检索和管理结构化数据，是信息系统的核心组件之一。",
    "分布式事务需要同时保证原子性、一致性、隔离性和持久性。",
    "全文检索通过倒排索引快速定位包含查询词的文档。",
    "他一边喝着咖啡，一边阅读关于量子计算的最新论文。",
    "气象台预报明天有中到大雨，局部地区有暴雨，请市民注意出行安全。",
    "小王昨天在超市买了三斤苹果、两瓶牛奶和一袋面包。",
    "长城是中国古代的军事防御工程，东起山海关，西至嘉峪关。",
    "会议定于下周三下午两点在三楼会议室召开，请各部门负责人准时参加。",
    "这部电影讲述了一个普通家庭在时代变迁中的悲欢离合。",
    "为了提高查询性能，优化器会根据统计信息选择代价最低的执行计划。",
    "张三和李四是大学同学，毕业后一起创办了一家软件公司。",
    "孩子们在操场上踢足球，老师在一旁认真地指导。",
    "黄河是中华民族的母亲河，发源于青藏高原的巴颜喀拉山脉。",
    "请把这份文件复印三份，然后交给财务部的刘经理。",
    "秋天的北京天高云淡，是一年中最适合旅游的季节。",
    "地铁十号线因设备故障临时停运，乘客可以换乘公交车。",
    "研究人员在深海发现了一种能够在高压环境下生存的新型微生物。",
    "图书馆新到了一批关于历史和哲学的书籍，欢迎大家借阅。",
    "这台服务器的内存使用率持续偏高，运维工程师正在排查原因。",
    "中医认为，饮食清淡、作息规律是保持身体健康的关键。",
    "奶奶每天早上都会去公园打太极拳，风雨无阻。",
    "由于连续降雨，河道水位上涨，防汛部门启动了应急预案。",
    "这篇论文提出了一种基于图神经网络的推荐算法。",
    "我们班的同学来自全国各地，有的来自东北，有的来自海南。",
    "他把钥匙忘在了办公室，只好打电话请同事帮忙送过来。",
    "新能源汽车的续航里程越来越长，充电设施也越来越完善。",
    "城市规划应当兼顾经济发展与生态环境保护。",
    "这款游戏的画面非常精美，但是操作有些复杂。",
    "考试前一天晚上，她复习到很晚才睡觉。",
    "在编译大型项目时，增量编译可以显著缩短等待时间。",
    "老张退休以后，在乡下种了一片菜园，过上了悠闲的生活。",
    "志愿者们在社区里为老年人提供免费的健康咨询服务。",
    "这座古镇保存完好，街道两旁都是明清时期的建筑。",
    "他的演讲条理清晰，赢得了台下观众的热烈掌声。",
    "公司决定明年在西南地区设立新的研发中心。",
    "夜深了，窗外的雨还在淅淅沥沥地下着。",
    "快递员说包裹已经放在小区门口的智能快递柜里了。",
    "这个季度的销售额比去年同期增长了一倍多。",
    "医生建议他每天坚持散步半小时，少吃油腻的食物。",
    "足球比赛进入加时赛后，主队终于攻入了制胜一球。",
    "我们需要对日志进行归档，以便以后排查问题。",
    "博物馆里展出了许多珍贵的青铜器和瓷器。",
    "这条高速公路连接了两座省会城市，大大缩短了通行时间。",
    "她在国外留学期间，学会了独立生活和解决问题。",
    "软件测试的目的是尽早发现缺陷，而不是证明程序没有错误。",
    "夏天的海边游客很多，沙滩上到处都是遮阳伞。",
    "事务提交之前，所有修改都只对当前会话可见。",
    "主键索引保证了每一行数据都可以被唯一地定位。",
    "这项政策的出台，受到了广大中小企业的欢迎。",
    "每逢佳节倍思亲，中秋节是家人团聚的日子。",
    "他从小就喜欢天文，梦想有一天能够成为宇航员。",
    "请不要在图书馆里大声喧哗。",
    "系统在凌晨两点自动执行数据备份任务。",
    "这家工厂引进了先进的自动化生产线，产量提高了三成。",
    "经过三年的努力，这座跨海大桥终于建成通车。",
    "虽然天气很冷，但是大家的热情一点也没有减少。",
    "缓存命中率下降之后，接口的平均响应时间明显变长。",
    "西湖的美景吸引了无数文人墨客留下诗篇。",
    "用户可以通过手机应用程序随时查询账户余额。",
    "如果输入的参数不合法，函数会返回错误码。",
    "他们计划明年春天去云南旅行，看看丽江古城和玉龙雪山。",
    "植物通过光合作用把二氧化碳和水转化为有机物。",
    "这家书店的咖啡区很安静，适合看书和写作。",
    "在并发场景下，多个线程同时修改共享变量可能导致数据竞争。",
    "足球、篮球和排球都是很受欢迎的球类运动。",
    "这座城市的地铁网络四通八达，出行十分便利。",
    "我昨天去银行办理了信用卡。",
    "这个问题可以从两个方面来分析。",
    "雨天路滑，请小心驾驶。",
    "他们是一个团结友爱的集体。",
    "请输入您的用户名和密码。",
    "我们一起去看电影吧。",
    "他因为身体原因辞去了主席职务。",
)

AMBIGUITY = (
    "中华人民共和国人民大会堂",
    "南京市长江大桥",
    "研究生命起源是一个古老而又充满挑战的课题。",
    "他们正在研究生命科学领域的前沿问题。",
    "下雨天留客天留我不留",
    "结婚的和尚未结婚的都可以报名参加。",
    "乒乓球拍卖完了",
    "羽毛球拍卖完了",
    "他说的确实在理",
    "发展中国家兔的饲养技术",
    "欢迎新老师生前来就餐",
    "这个门把手坏了请修理",
    "我们在野生动物园玩得很开心",
    "独立自主和平等互利的原则",
    "已经分房和尚未分房的同志",
    "他将来北京工作",
    "学生会宣传部举办了一场讲座",
    "北京大学生前来应聘",
    "使用户满意是我们的目标",
    "他从马上下来",
    "白天鹅在湖中游",
    "馆内陈列周恩来和邓颖超生前使用过的物品",
    "部分居民生活水平提高了",
    "人民币汇率保持基本稳定",
    "化妆和服装",
    "质量和服务",
    "从小学电脑",
    "企业家庭",
    "美国会通过对台售武法案",
    "小明硕士毕业于中国科学院计算所，后在日本京都大学深造",
)

CLASSICAL = (
    "床前明月光，疑是地上霜。举头望明月，低头思故乡。",
    "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。",
    "春眠不觉晓，处处闻啼鸟。夜来风雨声，花落知多少。",
    "学而时习之，不亦说乎？有朋自远方来，不亦乐乎？",
    "知之为知之，不知为不知，是知也。",
    "己所不欲，勿施于人。",
    "千里之行，始于足下。",
    "天下兴亡，匹夫有责。",
    "塞翁失马，焉知非福。",
    "路漫漫其修远兮，吾将上下而求索。",
    "海内存知己，天涯若比邻。",
    "不以规矩，不能成方圆。",
    "满招损，谦受益。",
    "画蛇添足 守株待兔 亡羊补牢 刻舟求剑 掩耳盗铃",
    "一石二鸟 一举两得 一心一意 三心二意 七上八下",
    "百闻不如一见 百尺竿头更进一步 九牛一毛 十全十美",
)

NAMES = (
    "北京 上海 广州 深圳 杭州 南京 成都 重庆 西安 武汉",
    "乌鲁木齐 呼和浩特 哈尔滨 石家庄 齐齐哈尔",
    "内蒙古自治区 新疆维吾尔自治区 香港特别行政区 澳门特别行政区",
    "中国人民银行 国家发展和改革委员会 中国科学院计算技术研究所",
    "清华大学 北京大学 复旦大学 浙江大学 中国科学技术大学",
    "联合国教科文组织 世界卫生组织 国际奥林匹克委员会",
    "诸葛亮 司马懿 欧阳修 爱新觉罗 上官婉儿",
    "麦当劳 肯德基 星巴克 可口可乐 百事可乐",
    "太平洋 大西洋 喜马拉雅山 珠穆朗玛峰 长江三角洲 珠江三角洲",
    "马克思 恩格斯 莎士比亚 贝多芬 爱因斯坦",
    "京沪高速铁路 粤港澳大湾区 长三角一体化",
    "中关村 陆家嘴 张江高科技园区",
)

TRADITIONAL = (
    "中華人民共和國是一個統一的多民族國家。",
    "資料庫系統的效能調校需要經驗。",
    "臺灣的高鐵非常方便。",
    "這個問題我們明天再討論。",
    "電腦軟體與網際網路。",
    "繁體字與簡體字的轉換。",
)

MIXED = (
    "OceanBase是一款稳定的分布式数据库，支持MySQL兼容模式。",
    "我在GitHub上找到了一个用Python3写的爬虫项目。",
    "iPhone15 Pro Max的售价是9999元。",
    "使用SQL语句SELECT * FROM t1 WHERE id=1查询数据。",
    "他的邮箱是zhangsan@example.com，电话是13800138000。",
    "请访问https://www.example.com/docs/index.html了解更多信息。",
    "2024年第3季度营收同比增长15.6%，净利润达到3.2亿元。",
    "这个API的QPS峰值达到了10万次每秒。",
    "在Linux系统中使用ls -la命令查看文件。",
    "5G网络和AI技术推动了IoT的发展。",
    "NBA总决赛G7湖人队以102比98获胜。",
    "维生素C和维生素B12对身体很重要。",
    "我们用Rust重写了C++代码，性能提升了30%。",
    "版本号从v4.2.1升级到v4.3.0之后，查询速度快了2倍。",
    "北京时间2024-06-01 08:30:00发布。",
    "这款手机支持Wi-Fi 6和蓝牙5.3。",
    "用户名admin_01登录失败了3次。",
    "Hello世界，你好World！",
    "中文English混合123数字。",
    "我的IP地址是192.168.1.100，端口是3306。",
    "下载速度只有2.5MB/s，太慢了。",
    "这台电脑配置了32GB内存和1TB固态硬盘。",
    "本次考试满分100分，他考了98.5分。",
    "温度计显示现在是-5℃，体感温度更低。",
    "这个项目的预算是500万美元，约合3600万人民币。",
    "请在12月31日23:59之前提交申请。",
    "K8s集群里有128个Pod正在运行。",
    "他在B站发布了一个关于C#编程的视频。",
    "T恤的尺码有S、M、L和XL。",
    "卡拉OK和KTV在年轻人中很流行。",
    "这本书的ISBN是978-7-111-12345-6。",
    "快递单号SF1234567890已签收。",
    "我们用utf8mb4字符集存储emoji表情。",
    "x86_64和arm64是两种常见的CPU架构。",
    "JSON格式的数据比XML更简洁。",
    "他用Excel做了一张销售报表。",
    "A股和H股同时上市。",
    "PM2.5浓度超过了75微克每立方米。",
    "第1名到第10名都有奖品。",
    "上午9:30开会，下午3点培训。",
    "我买了2斤苹果和3.5公斤大米。",
    "本产品的保质期为18个月。",
    "他跑完42.195公里的马拉松用了3小时15分。",
    "高铁时速可以达到350公里。",
    "你可以用Ctrl+C复制，用Ctrl+V粘贴。",
    "张三的工号是E10086，部门是R&D。",
    "这个Bug在V2.0.1版本中已经修复。",
    "COVID-19疫情对全球经济造成了巨大影响。",
    "DNA和RNA是两种重要的核酸。",
    "3D打印技术可以制造复杂的零件。",
    "MP3、MP4和AVI都是常见的多媒体格式。",
    "我在Stack Overflow上提了一个问题。",
)

NUMBERS = (
    "一二三四五六七八九十",
    "零一二三四五六七八九",
    "两千零二十四年十二月三十一日",
    "壹佰贰拾叁万肆仟伍佰陆拾柒元捌角玖分",
    "三百五十个 五公斤 十米 八千米 一百块钱",
    "两杯咖啡 三只小猫 四本书 五辆汽车 六张桌子",
    "第一名 第二十三届 第3次 第100位",
    "百分之八十 千分之五 三分之一 万分之一",
    "1个 23个 456个 7890个",
    "1.5公斤 3.14159 2,000,000 1,234.56元",
    "9点30分 2024年1月1日 12月25日",
    "5.1亿人次 100%的 50%以上",
    "几十个 数百名 上千人 成千上万 一万多",
    "廿卅 卅年 廿一世纪",
    "零点五 负三 正负零",
    "二〇二四年",
    "3千万 5万 7亿8千万",
    "一二三1234五六七",
    "1,2,3 1.2.3 1..2 .5 5. ,5 5, 1,,2 1.,2",
    "０１２３４５６７８９ １２．５ ３，０００",
    "12个人 3人 5位客人 7名学生",
    "一个一个地 一次又一次",
    "十二点三十分 下午三点钟 八小时 三十秒钟",
    "三平方公里 五立方厘米 二十世纪 五周年 一百海里",
    "八千万 九十九 一百零一 一千零一夜",
    "三五成群 一五一十 七七八八",
    "两个 两只 两斤 两岁",
    "1000 1,000 1.000 1_000",
    "3.14.15 1,23,456 0.001 00001",
    "123abc abc123 a1b2c3 1a 1-2 1+1",
    "十万八千里 九九八十一",
    "万亿 亿万 千千万万",
    "12345678901234567890",
    "3个半小时 两年半 一个半月",
    "5%的人 30%-40% 1:2 3/4",
    "零下十度 三十七度五",
    "二零二四年一月一日",
    "公元前221年",
    "1949年10月1日",
    "7×24小时 365天",
    "¥100 $200 €300 ￥400",
    "三千五百万元 八亿七千万人",
    "100万 2.5亿 3千万",
    "1.2亿 4亿 110个",
    "3三个 3个 三个 3.5个 三点五个",
    "一个 1个 一1个 1一个",
    "5公斤 5 公斤 五 公斤",
    "2千克 3千米 4平方米 5立方米 6平方英尺",
)

LETTERS = (
    "Hello World",
    "hello world HELLO WORLD HeLLo WoRLd",
    "The quick brown fox jumps over the lazy dog.",
    "It's a beautiful day; isn't it?",
    "camelCaseWord PascalCase snake_case kebab-case SCREAMING_SNAKE_CASE",
    "C++ C# F# .NET Node.js ASP.NET Vue.js",
    "AT&T R&D Q&A A+B a+b=c",
    "john.doe@example.com first_last@sub.domain.org",
    "https://www.example.com/path/to/page?query=1&lang=zh#top",
    "/usr/local/bin C:\\Windows\\System32 ./a.out ../x.txt",
    "v1.2.3 V4.2.1-beta 2.0.0-rc.1 python3.12",
    "192.168.1.1 10.0.0.255 ::1 fe80::1",
    "+86-138-0013-8000 (010)12345678",
    "$100 USD100 100USD",
    "#OceanBase @user #hashtag @mention",
    "_a a_ __init__ a__b _ __ a_1 1_a 1_2",
    "a- -a a-- --a a-b-c a.b.c a..b a._b a@@b",
    "pneumonoultramicroscopicsilicovolcanoconiosis",
    "supercalifragilisticexpialidocious",
    "iPhone15 iPad2 COVID19 H1N1 MP3 4K 5G 3D x86_64 arm64 utf8mb4",
    "ＡＢＣ ａｂｃ Ｈｅｌｌｏ\u3000Ｗｏｒｌｄ ＯｃｅａｎＢａｓｅ",
    "ＡＢＣ１２３ ａ＿ｂ ａ_ｂ a＿b",
    "A B C D E F G",
    "a b c d e f g",
    "ABC-123 abc_123 abc.123 abc@123 abc#123 abc&123 abc+123",
    "123-abc 123_abc 123.abc 123@abc 123#abc 123&abc 123+abc",
    "--- ... ___ @@@ ### &&& +++",
    "e.g. i.e. etc. vs. a.m. p.m.",
    "O'Reilly rock'n'roll don't won't",
    "well-known state-of-the-art up-to-date",
    "foo.bar.baz foo@bar.baz foo#bar foo&bar foo+bar foo_bar foo-bar",
    "A.B.C. U.S.A. U.K.",
    "x=1;y=2;z=x+y",
    "if(a>b){return a;}else{return b;}",
    "SELECT id, name FROM users WHERE age >= 18 ORDER BY id DESC;",
    "The IK analyzer splits text into tokens.",
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
    "MiXeD CaSe WoRdS",
    "ALLCAPS lowercase Titlecase",
    "a1 b22 c333 d4444",
)

CHARS = (
    "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
    "，。、；：？！“”‘’（）《》【】…—·～",
    "！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～",
    "你好，世界！Hello, world!",
    "你好,世界!Hello,world!",
    "你好\u3000世界",
    "你好\t世界\n再见\r朋友",
    "你 好 世 界",
    "你好\u00a0世界",
    "你好\u200b世界",
    "\ufeff你好",
    "東京は日本の首都です。",
    "ひらがな カタカナ",
    "こんにちは、世界！",
    "日本語の文章と中文句子。",
    "안녕하세요 세계",
    "대한민국 서울특별시",
    "\u1100\u1101\u1102 ㄱㄴㄷ",
    "ｱｲｳｴｵ ﾊﾟﾋﾟ",
    "ㇰㇱㇲ",
    "㐀㐁㐂",
    "豈更車",
    "\U00020000\U00020001\U00020002",
    "〇〆々〻",
    "ㄅㄆㄇㄈ",
    "⺀⺁⺂",
    "😀🎉👍🏻❤️",
    "中国🇨🇳加油💪",
    "￥100 ￡200 ￠ ￢ ￣ ￤ ￦",
    "｡｢｣､",
    "｟｠",
    "ÀÉÎÕÜ àéîõü café naïve",
    "ΑΒΓΔ αβγδ",
    "АБВГ абвг",
    "עברית العربية हिन्दी ไทย",
    "①②③ ⅠⅡⅢ ⑴⑵⑶",
    "㈠㈡ ㊀㊁",
    "℃ ℉ ㎡ ㎏",
    "ａb１2",
    "中文\x00字符",
)

EDGE = (
    "",
    " ",
    " \t\n ",
    "，。！",
    "a",
    "1",
    "中",
    "ア",
    "가",
    "一",
    "个",
    "的",
    "ｱ",
    "＃",
    "￥",
    "_",
    "__",
    "a_",
    "_a",
    "的的的的的的的的的的",
    "测试测试测试测试",
    "哈哈哈哈哈哈哈哈",
    "aaaa aaaa aaaa",
    "中国中国中国人民人民",
    "好好学习天天向上",
    "ABC abc Abc aBC",
    "ＡＢＣ ａｂｃ",
)

BIN_COLLATION = (
    "Hello WORLD 中国",
    "ＡＢＣ ａｂｃ Ａｂｃ",
    "snake_CASE Kebab-Case",
    "中华人民共和国人民大会堂",
    "OceanBase数据库V4.2发布",
    "ABC abc Abc aBC",
)

COLUMN_TEXTS = (
    "中华人民共和国人民大会堂",
    "南京市长江大桥",
    "OceanBase是一款稳定的分布式数据库，支持MySQL兼容模式。",
    "snake_case和kebab-case",
    "三百五十个 五公斤 十米",
    "",
    None,
    "，。！",
    "안녕하세요 세계",
    "ABC abc Abc aBC",
)

INDEX_DOCS = (
    "中华人民共和国人民大会堂",
    "南京市长江大桥",
    "研究生命起源是一个古老而又充满挑战的课题。",
    "结婚的和尚未结婚的都可以报名参加。",
    "乒乓球拍卖完了",
    "OceanBase是一款稳定的分布式数据库，支持MySQL兼容模式。",
    "我们用Rust重写了C++代码，性能提升了30%。",
    "版本号从v4.2.1升级到v4.3.0之后，查询速度快了2倍。",
    "用户名admin_01登录失败了3次。",
    "camelCaseWord PascalCase snake_case kebab-case SCREAMING_SNAKE_CASE",
    "john.doe@example.com first_last@sub.domain.org",
    "三百五十个 五公斤 十米 八千米 一百块钱",
    "2024年第3季度营收同比增长15.6%，净利润达到3.2亿元。",
    "1,2,3 1.2.3 1..2 .5 5. ,5 5, 1,,2 1.,2",
    "東京は日本の首都です。こんにちは、世界！",
    "안녕하세요 세계 대한민국 서울특별시",
    "ｱｲｳｴｵ ﾊﾟﾋﾟ",
    "ＡＢＣ１２３ ａ＿ｂ ａ_ｂ a＿b",
    "Hello世界，你好World！",
    "床前明月光，疑是地上霜。举头望明月，低头思故乡。",
    "",
    None,
    "，。！",
    "😀🎉👍🏻 中国🇨🇳加油",
    "的的的的的的的的的的",
    "他说的确实在理",
    "北京大学生前来应聘",
    "发展中国家兔的饲养技术",
    "已经分房和尚未分房的同志",
    "x86_64和arm64是两种常见的CPU架构。",
)

INDEX_TERMS = (
    "人民",
    "人民大会堂",
    "中华人民共和国",
    "共和国",
    "长江大桥",
    "大桥",
    "市长",
    "南京市",
    "研究生",
    "生命",
    "和尚",
    "尚未",
    "乒乓球",
    "球拍",
    "拍卖",
    "oceanbase",
    "OceanBase",
    "mysql",
    "rust",
    "c",
    "v4.2.1",
    "admin_01",
    "admin",
    "snake_case",
    "snake",
    "screaming_snake_case",
    "三百五十个",
    "五公斤",
    "公斤",
    "15.6",
    "3.2亿",
    "亿",
    "1.2.3",
    "5",
    "東京",
    "こ",
    "안",
    "서울특별시",
    "ｱ",
    "ａ_ｂ",
    "世界",
    "world",
    "明月",
    "的",
    "确实",
    "实在",
    "大学生",
    "国家",
    "家兔",
    "分房",
    "x86_64",
    "cpu",
    "加油",
)

INDEX_NATURAL = (
    "中华人民共和国人民大会堂",
    "南京市长江大桥",
    "研究生命起源",
    "结婚的和尚",
    "OceanBase数据库",
    "snake_case命名规范",
    "三百五十个苹果",
    "안녕하세요 세계",
    "こんにちは世界",
    "，。！",
    "hello world",
    "x86_64架构",
    "2024年营收",
    "明月光",
)

INDEX_BOOLEAN = (
    "+人民 -南京",
    "人民 长江",
    "+中华人民共和国 +人民大会堂",
    "\"南京市 长江大桥\"",
    "a.b@c.com",
)

INDEX_UPDATES = (
    (2, "北京大学生前来应聘"),
    (6, "OceanBase数据库V4.2发布"),
    (10, "snake_case_v2 kebab-case-v2"),
)

INDEX_DELETES = (1, 16)

INDEX_INSERTS = (
    (31, "中华人民共和国人民大会堂"),
    (32, "안녕하세요 대한민국"),
)

INDEX_REQUERY = (
    "人民",
    "人民大会堂",
    "大学生",
    "长江大桥",
    "oceanbase",
    "v4.2",
    "snake_case",
    "snake_case_v2",
    "안",
    "대한민국",
)


def literal(text):
    if text is None:
        return "NULL"
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "'":
            out.append("''")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\x00":
            out.append("\\0")
        else:
            out.append(ch)
    return "'" + "".join(out) + "'"


def tok(expression, props=None, parser="'ik'"):
    if props is None:
        return "tokenize({}, {})".format(expression, parser)
    return "tokenize({}, {}, {})".format(expression, parser, literal(props))


def stmt(sql, errno=0, kind="setup", extra=None):
    return Statement(sql, errno, kind, extra)


def mode_probes(expression):
    return [
        stmt("SELECT {} AS {}".format(tok(expression, props), name), 0, "all")
        for name, props in MODES
    ]


def default_probe(expression):
    return stmt("SELECT {} AS t".format(tok(expression)), 0, "default")


def text_units(texts, with_default=False, collate=None):
    units = []
    for text in texts:
        expression = literal(text)
        if collate:
            expression = "{} COLLATE {}".format(expression, collate)
        unit = mode_probes(expression)
        if with_default:
            unit.append(default_probe(expression))
        units.append(unit)
    return units


def comment(text):
    return "--echo # {}".format(text)


def error_units():
    zh = literal("中华人民共和国")
    probes = [
        comment("inputs that are not utf8mb4 text"),
        stmt("SELECT {} AS t".format(tok("NULL")), 1235, "error"),
        stmt("SELECT {} AS t".format(tok("NULL", SMART)), 1235, "error"),
        stmt("SELECT {} AS t".format(tok("_binary " + zh)), 1235, "error"),
        stmt("SELECT {} AS t".format(tok("CAST({} AS BINARY)".format(zh))), 1235, "error"),
        stmt("SELECT {} AS t".format(tok("X'E4B8AD'")), 1235, "error"),
        stmt("SELECT {} AS t".format(tok("123")), 1235, "error"),
        stmt("SELECT {} AS t".format(tok("12.5")), 1235, "error"),
        stmt("SELECT {} AS t".format(tok("_utf8mb4 X'E4B8'")), 1300, "error"),
        stmt("SELECT {} AS t".format(tok("_utf8mb4 X'E4B8ADE59BBD'", SMART)), 0, "all"),
        comment("parser names"),
        stmt("SELECT {} AS t".format(tok(zh, parser="'IK'")), 0, "default"),
        stmt("SELECT {} AS t".format(tok(zh, parser="' ik '")), 0, "default"),
        stmt("SELECT {} AS t".format(tok(zh, parser="_utf8mb4 'ik' COLLATE utf8mb4_bin")), 0, "default"),
        stmt("SELECT {} AS t".format(tok(zh, parser="CONCAT('i', 'k')")), 0, "default"),
        stmt("SELECT {} AS t".format(tok(zh, parser="'ik.1'")), 1128, "error"),
        stmt("SELECT {} AS t".format(tok(zh, parser="'ikk'")), 1128, "error"),
        stmt("SELECT {} AS t".format(tok(zh, parser="'i k'")), 1128, "error"),
        stmt("SELECT {} AS t".format(tok(zh, parser="''")), 1210, "error"),
        stmt("SELECT {} AS t".format(tok(zh, parser="' '")), 1210, "error"),
        stmt("SELECT tokenize() AS t", 1582, "error"),
        stmt("SELECT tokenize({}, 'ik', {}, 'x') AS t".format(zh, literal(SMART)), 1582, "error"),
    ]
    good_props = (
        ('[{"output": "default"}]', "default"),
        ('[{"output": "all"}]', "all"),
        ('[{"Output": "ALL"}]', "all"),
        ("[]", "default"),
        ("[{}]", "default"),
        ('[{"additional_args": []}]', "default"),
        ('[{"additional_args": [{"ik_mode": "SMART"}]}]', "default"),
        ('[{"additional_args": [{"ik_mode": "Max_Word"}]}]', "default"),
        ('[{"additional_args": [{"ik_mode": "smart"}, {"ik_mode": "max_word"}]}]', "default"),
        ('[{"additional_args": [{"ik_mode": "max_word"}, {"ik_mode": "smart"}]}]', "default"),
        ('[{"additional_args": [{"IK_MODE": "max_word"}]}]', "default"),
        ('[{"additional_args": [{"dict_table": "x"}]}]', "default"),
        ('[{"additional_args": [{"quanitfier_table": "x"}]}]', "default"),
        ('[{"additional_args": [{"stopword_table": "y"}]}]', "default"),
        ('[{"additional_args": [{"Ik_Mode": "bad"}]}]', "default"),
        ('[{"additional_args": [{"IK_MODE": "max_word"}, {"ik_mode": "max_word"}]}]', "default"),
        ('[{"output": "all", "additional_args": [{"ik_mode": "max_word"}]}]', "all"),
        ('[{"additional_args": [{"ik_mode": "max_word"}]}, {"output": "all"}]', "all"),
        ('[{"output": "all"}, {"output": "default"}]', "default"),
    )
    bad_props = (
        ('[{"additional_args": [{"ik_mode": ""}]}]', 1210),
        ('[{"additional_args": [{"ik_mode": null}]}]', 1210),
        ('[{"additional_args": [{"ik_mode": 123}]}]', 1210),
        ('[{"additional_args": [{"ik_mode": ["smart"]}]}]', 1210),
        ('[{"additional_args": [{"ik_mode": "bad"}]}]', 1210),
        ('[{"additional_args": [{"ik_mode": "max_word"}, {"ik_mode": 1}]}]', 1210),
        ('[{"additional_args": [{"ik_mode": "max_word", "output": "all"}]}]', 1210),
        ('[{"additional_args": {"ik_mode": "max_word"}}]', 1210),
        ('[{"additional_args": [{"min_token_size": 3}]}]', 1235),
        ('[{"additional_args": [{"quantifier_table": "x"}, {"stopword_table": "y"}]}]', 1235),
        ('[{"additional_args": [{"quantifier_table": "x"}]}]', 1235),
        ('[{"additional_args": [{"ik_mode": " smart"}]}]', 1210),
        ('[{"output": "defaults"}]', 1210),
        ('[{"case": "upper"}]', 1235),
        ('[{"stopwords": "a"}]', 1235),
        ('[{"undefined": "all"}]', 1210),
        ("{}", 1210),
        ('["all"]', 1210),
        ("not json", 3141),
    )
    props_units = [comment("the third argument")]
    for props, kind in good_props:
        props_units.append(stmt("SELECT {} AS t".format(tok(zh, props)), 0, kind))
    for props, errno in bad_props:
        props_units.append(stmt("SELECT {} AS t".format(tok(zh, props)), errno, "error"))
    json_units = [
        comment("the token list as a JSON value"),
        stmt("SELECT JSON_LENGTH({}) AS n".format(tok(zh, MAX_WORD_DEFAULT)), 0, "value"),
        stmt("SELECT {} MEMBER OF ({}) AS m".format(literal("人民"), tok(zh, MAX_WORD_DEFAULT)), 0, "value"),
        stmt("SELECT {} MEMBER OF ({}) AS m".format(literal("人民"), tok(zh)), 0, "value"),
        stmt("SELECT JSON_EXTRACT({}, '$.doc_len') AS d".format(tok(zh, MAX_WORD)), 0, "value"),
        stmt("SELECT JSON_KEYS({}) AS k".format(tok(zh, SMART)), 0, "value"),
        stmt("SELECT JSON_TYPE({}) AS t".format(tok(zh)), 0, "value"),
    ]
    return [probes, props_units, json_units]


def collation_units():
    units = [[comment("utf8mb4_bin input")]]
    units.extend(text_units(BIN_COLLATION, with_default=True, collate="utf8mb4_bin"))
    return units


def column_units():
    rows = []
    for index, text in enumerate(COLUMN_TEXTS, 1):
        rows.append("({}, {}, {})".format(index, literal(text), literal(text)))
    count = len(COLUMN_TEXTS)
    unit = [
        comment("VARCHAR columns read from a table"),
        stmt("CREATE TABLE ik_s6_col (id INT PRIMARY KEY, c VARCHAR(1000), cb VARCHAR(1000) COLLATE utf8mb4_bin)"),
        stmt("INSERT INTO ik_s6_col VALUES {}".format(", ".join(rows))),
    ]
    for name, props in MODES:
        unit.append(
            stmt("SELECT id, {} AS {} FROM ik_s6_col WHERE c IS NOT NULL ORDER BY id".format(tok("c", props), name),
                 0, "rows_all", count - 1)
        )
        unit.append(
            stmt("SELECT id, {} AS {} FROM ik_s6_col WHERE cb IS NOT NULL ORDER BY id".format(tok("cb", props), name),
                 0, "rows_all", count - 1)
        )
    unit.append(stmt("SELECT id, {} AS t FROM ik_s6_col WHERE c IS NOT NULL ORDER BY id".format(tok("c")),
                     0, "rows_default", count - 1))
    unit.append(stmt("SELECT id, {} AS t FROM ik_s6_col WHERE c IS NOT NULL ORDER BY id".format(tok("c", MAX_WORD_DEFAULT)),
                     0, "rows_default", count - 1))
    unit.append(stmt("SELECT id, {} AS t FROM ik_s6_col WHERE id = 7".format(tok("c")), 0, "rows_default", 1))
    unit.append(stmt("SELECT id FROM ik_s6_col WHERE {} MEMBER OF ({}) ORDER BY id".format(
        literal("人民"), tok("c", MAX_WORD_DEFAULT)), 0, "ids"))
    unit.append(stmt("SELECT id FROM ik_s6_col WHERE {} MEMBER OF ({}) ORDER BY id".format(
        literal("snake_case"), tok("c")), 0, "ids"))
    unit.append(stmt("DROP TABLE ik_s6_col"))
    text_unit = [
        comment("a TEXT column: TOKENIZE reads the LOB locator, not the text"),
        stmt("CREATE TABLE ik_s6_text (id INT PRIMARY KEY, c TEXT)"),
        stmt("INSERT INTO ik_s6_text VALUES (1, {}), (2, {})".format(literal("中华人民共和国"), literal("hello world"))),
        stmt("SELECT id, {} AS t FROM ik_s6_text WHERE id = 1".format(tok("c", SMART)), 1210, "error"),
        stmt("SELECT id, {} AS t FROM ik_s6_text WHERE id = 2".format(tok("c")), 1210, "error"),
        stmt("SELECT id, {} AS t FROM ik_s6_text WHERE id = 1".format(tok("CAST(c AS CHAR(100))", SMART)), 0, "rows_all", 1),
        stmt("DROP TABLE ik_s6_text"),
        comment("TOKENIZE in a generated column"),
        stmt("CREATE TABLE ik_s6_gen (id INT PRIMARY KEY, c VARCHAR(100), t JSON GENERATED ALWAYS AS ({}) STORED)".format(
            tok("c")), 4016, "error"),
    ]
    return [unit, text_unit]


def edge_units():
    units = [[comment("short and degenerate inputs")]]
    units.extend(text_units(EDGE, with_default=True))
    units.extend(error_units())
    units.extend(collation_units())
    units.extend(column_units())
    return units


class Long(object):
    def __init__(self, sql, text):
        self.sql = sql
        self.text = text


def lit_long(text):
    return Long(literal(text), text)


def rep(part, count):
    if isinstance(part, str):
        part = lit_long(part)
    return Long("REPEAT({}, {})".format(part.sql, count), part.text * count)


def cat(*parts):
    parts = [lit_long(part) if isinstance(part, str) else part for part in parts]
    return Long("CONCAT({})".format(", ".join(part.sql for part in parts)), "".join(part.text for part in parts))


def long_texts():
    unit10 = "人工智能技术发展迅速"
    if len(unit10) != 10:
        raise GeneratorError("the 10-character unit is {} characters".format(len(unit10)))
    paragraph = "".join(PROSE)
    return (
        ("1000 Chinese characters, no separator: one batch", rep(unit10, 100)),
        ("a space at character 1000: one batch", cat(rep(unit10, 100), " ", unit10)),
        ("a space at character 1001: two batches", cat(rep(unit10, 100), "人", " ", unit10)),
        ("a space at character 1002: two batches", cat(rep(unit10, 100), "人工", " ", unit10)),
        ("an ideographic full stop after every 10 characters", rep(unit10 + "。", 300)),
        ("a fullwidth comma after every 10 characters: not a separator", rep(unit10 + "，", 300)),
        ("English words", rep("hello world ", 500)),
        ("mixed text", rep("OceanBase数据库V4.2发布，性能提升30%。", 100)),
        ("one English token of 5000 letters", rep("a", 5000)),
        ("one number of 3000 digits", rep("1", 3000)),
        ("one fullwidth token of 2000 letters", rep("ａ", 2000)),
        ("a four-character word and a space, 250 times", rep("中国人民 ", 250)),
        ("the prose sentences as one paragraph", lit_long(paragraph)),
        ("the paragraph seven times", rep(paragraph, 7)),
        ("a Chinese number and quantifier, 250 times, no separator", rep("三百五十个", 250)),
        ("a number, a quantifier and a space, 400 times", rep("3个 ", 400)),
        ("a space at character 1001 after short English words", cat(rep("ab ", 334), "cd")),
        ("Hangul words and spaces", rep("한국어 ", 300)),
        ("emoji only", rep("😀", 1500)),
        ("underscore identifiers", rep("snake_case_name ", 200)),
    )


def long_units():
    units = []
    header = [
        comment("long inputs, stored in a VARCHAR column"),
        stmt("CREATE TABLE ik_s7_long (id INT PRIMARY KEY, c VARCHAR(40000))"),
    ]
    body = []
    for index, (title, value) in enumerate(long_texts(), 1):
        length = len(value.text)
        if length > 40000:
            raise GeneratorError("long text {} has {} characters".format(index, length))
        body.append(comment("{}: {}".format(index, title)))
        body.append(stmt("INSERT INTO ik_s7_long VALUES ({}, {})".format(index, value.sql)))
        for name, props in MODES:
            body.append(
                stmt("SELECT id, CHAR_LENGTH(c) AS n, {} AS {} FROM ik_s7_long WHERE id = {}".format(
                    tok("c", props), name, index), 0, "long", length)
            )
    units.append(header + body + [stmt("DROP TABLE ik_s7_long")])
    return units


def ids_query(table, column, against, boolean):
    mode = " IN BOOLEAN MODE" if boolean else ""
    match = "MATCH({}) AGAINST({}{})".format(column, literal(against), mode)
    return "SELECT id, {} AS score FROM {} WHERE {} ORDER BY id".format(match, table, match)


def index_main_unit():
    table = "ik_s8_doc"
    unit = [
        comment("a smart and a max_word fulltext index on one table"),
        stmt("CREATE TABLE {} (id INT PRIMARY KEY, cs VARCHAR(4000), cm VARCHAR(4000), "
             "FULLTEXT INDEX fs(cs) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='smart'), "
             "FULLTEXT INDEX fm(cm) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='max_word'))".format(table)),
        stmt("SHOW CREATE TABLE {}".format(table), 0, "value"),
    ]
    for index, text in enumerate(INDEX_DOCS, 1):
        unit.append(stmt("INSERT INTO {} VALUES ({}, {}, {})".format(table, index, literal(text), literal(text))))
    unit.append(comment("single terms in boolean mode"))
    for term in INDEX_TERMS:
        for column in ("cs", "cm"):
            unit.append(stmt(ids_query(table, column, term, True), 0, "ids"))
    unit.append(comment("natural language mode: the query text goes through the index's parser"))
    for query in INDEX_NATURAL:
        for column in ("cs", "cm"):
            unit.append(stmt(ids_query(table, column, query, False), 0, "ids"))
    unit.append(comment("boolean operators"))
    for query in INDEX_BOOLEAN:
        errno = 1149 if "@" in query else 0
        unit.append(stmt(ids_query(table, "cs", query, True), errno, "ids" if errno == 0 else "error"))
    unit.append(comment("DML on the indexed table"))
    for index, text in INDEX_UPDATES:
        unit.append(stmt("UPDATE {} SET cs = {}, cm = {} WHERE id = {}".format(table, literal(text), literal(text), index)))
    for index in INDEX_DELETES:
        unit.append(stmt("DELETE FROM {} WHERE id = {}".format(table, index)))
    for index, text in INDEX_INSERTS:
        unit.append(stmt("INSERT INTO {} VALUES ({}, {}, {})".format(table, index, literal(text), literal(text))))
    for term in INDEX_REQUERY:
        for column in ("cs", "cm"):
            unit.append(stmt(ids_query(table, column, term, True), 0, "ids"))
    unit.append(stmt("DROP TABLE {}".format(table)))
    return unit


def index_late_unit():
    unit = [
        comment("an index created on a table that already holds the documents"),
        stmt("CREATE TABLE ik_s8_late (id INT PRIMARY KEY, c VARCHAR(4000))"),
    ]
    for index, text in enumerate(INDEX_DOCS, 1):
        unit.append(stmt("INSERT INTO ik_s8_late VALUES ({}, {})".format(index, literal(text))))
    unit.append(stmt("CREATE FULLTEXT INDEX fl ON ik_s8_late(c) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='max_word')"))
    for term in INDEX_TERMS[:20] + ("snake_case", "안", "的"):
        unit.append(stmt(ids_query("ik_s8_late", "c", term, True), 0, "ids"))
    for query in INDEX_NATURAL[:6]:
        unit.append(stmt(ids_query("ik_s8_late", "c", query, False), 0, "ids"))
    unit.append(stmt("DROP TABLE ik_s8_late"))
    unit.append(comment("a TEXT column with an IK index: the index reads the text"))
    unit.append(stmt("CREATE TABLE ik_s8_text (id INT PRIMARY KEY, c TEXT, FULLTEXT INDEX ft(c) WITH PARSER ik)"))
    for index, text in enumerate(INDEX_DOCS[:12], 1):
        unit.append(stmt("INSERT INTO ik_s8_text VALUES ({}, {})".format(index, literal(text))))
    for term in ("人民大会堂", "人民", "长江大桥", "oceanbase", "snake_case", "三百五十个", "2"):
        unit.append(stmt(ids_query("ik_s8_text", "c", term, True), 0, "ids"))
    for query in INDEX_NATURAL[:4]:
        unit.append(stmt(ids_query("ik_s8_text", "c", query, False), 0, "ids"))
    unit.append(stmt("SELECT id, {} AS t FROM ik_s8_text WHERE id = 1".format(tok("c")), 1210, "error"))
    unit.append(stmt("DROP TABLE ik_s8_text"))
    return unit


def index_edge_unit():
    unit = [
        comment("index DDL that the IK parser refuses"),
        stmt("CREATE TABLE ik_s8_bad1 (id INT PRIMARY KEY, c VARCHAR(100), FULLTEXT INDEX f(c) WITH PARSER ik "
             "PARSER_PROPERTIES=(ik_mode='bad'))", 1210, "error"),
        stmt("CREATE TABLE ik_s8_bad2 (id INT PRIMARY KEY, c VARCHAR(100), FULLTEXT INDEX f(c) WITH PARSER ik "
             "PARSER_PROPERTIES=(min_token_size=2))", 1235, "error"),
        stmt("CREATE TABLE ik_s8_bad3 (id INT PRIMARY KEY, c VARBINARY(100), FULLTEXT INDEX f(c) WITH PARSER ik)",
             1283, "error"),
        comment("the default mode of an IK index, and ik_mode in capitals"),
        stmt("CREATE TABLE ik_s8_mode (id INT PRIMARY KEY, a VARCHAR(100), b VARCHAR(100), "
             "FULLTEXT INDEX fa(a) WITH PARSER ik, "
             "FULLTEXT INDEX fb(b) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='MAX_WORD'))"),
        stmt("SHOW CREATE TABLE ik_s8_mode", 0, "value"),
        stmt("INSERT INTO ik_s8_mode VALUES (1, {}, {})".format(literal("中华人民共和国"), literal("中华人民共和国"))),
        stmt(ids_query("ik_s8_mode", "a", "人民", True), 0, "ids"),
        stmt(ids_query("ik_s8_mode", "b", "人民", True), 0, "ids"),
        stmt("DROP TABLE ik_s8_mode"),
        comment("long documents in an index"),
        stmt("CREATE TABLE ik_s8_long (id INT PRIMARY KEY, cs VARCHAR(40000), cm VARCHAR(40000), "
             "FULLTEXT INDEX fs(cs) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='smart'), "
             "FULLTEXT INDEX fm(cm) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='max_word'))"),
    ]
    docs = (
        rep("中国人民 ", 250),
        rep("人工智能技术发展迅速。", 300),
        rep("a", 5000),
        cat(rep("ab ", 334), "cd"),
    )
    for index, value in enumerate(docs, 1):
        unit.append(stmt("INSERT INTO ik_s8_long VALUES ({}, {}, {})".format(index, value.sql, value.sql)))
    for term in ("中国人民", "民", "人工智能", "迅", "cd", "ab"):
        for column in ("cs", "cm"):
            unit.append(stmt(ids_query("ik_s8_long", column, term, True), 0, "ids"))
    long_match = "MATCH(cs) AGAINST({} IN BOOLEAN MODE)".format(rep("a", 5000).sql)
    unit.append(stmt("SELECT id, {} AS score FROM ik_s8_long WHERE {} ORDER BY id".format(long_match, long_match), 0, "ids"))
    unit.append(stmt("DROP TABLE ik_s8_long"))
    return unit


def section_units():
    s1 = [[comment("sentences")]] + text_units(PROSE)
    s1 += [[comment("sentences whose segmentation is ambiguous")]] + text_units(AMBIGUITY, with_default=True)
    s1 += [[comment("classical Chinese and idioms")]] + text_units(CLASSICAL)
    s1 += [[comment("names of places, organizations and people")]] + text_units(NAMES)
    s1 += [[comment("traditional characters")]] + text_units(TRADITIONAL)
    return OrderedDict(
        (
            ("s1_cjk", s1),
            ("s2_mixed", [[comment("Chinese mixed with letters and numbers")]] + text_units(MIXED)),
            ("s3_numbers", [[comment("numbers and quantifiers")]] + text_units(NUMBERS)),
            ("s4_letters", [[comment("letters and connectors")]] + text_units(LETTERS)),
            ("s5_chars", [[comment("character classes")]] + text_units(CHARS)),
            ("s6_edge", edge_units()),
            ("s7_long", long_units()),
            ("s8_index", [index_main_unit(), index_late_unit(), index_edge_unit()]),
        )
    )


def unit_size(unit):
    return sum(1 for item in unit if isinstance(item, Statement))


def pack_units(units, limit):
    files = []
    current = []
    size = 0
    pending = []
    for unit in units:
        if unit_size(unit) == 0:
            pending.extend(unit)
            continue
        unit = pending + list(unit)
        pending = []
        if current and size + unit_size(unit) > limit:
            files.append(current)
            current = []
            size = 0
        current.extend(unit)
        size += unit_size(unit)
    if pending:
        current.extend(pending)
    if current:
        files.append(current)
    return files


def check_statement(statement):
    sql = statement.sql
    if "\n" in sql or "\r" in sql:
        raise GeneratorError("a statement spans lines: {!r}".format(sql[:80]))
    if sql.endswith(";"):
        raise GeneratorError("a statement ends with the delimiter: {!r}".format(sql[:80]))
    if sql.lstrip() != sql or sql.startswith("--") or sql.startswith("#"):
        raise GeneratorError("a statement starts like a mysqltest command: {!r}".format(sql[:80]))
    quote = None
    index = 0
    while index < len(sql):
        ch = sql[index]
        if quote:
            if ch == "\\":
                index += 2
                continue
            if ch == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        index += 1
    if quote:
        raise GeneratorError("unbalanced quotes: {!r}".format(sql[:80]))
    if statement.kind == "error" and statement.errno == 0:
        raise GeneratorError("an error statement expects errno 0: {!r}".format(sql[:80]))
    if statement.kind != "error" and statement.errno != 0:
        raise GeneratorError("a statement that expects an error is not marked as one: {!r}".format(sql[:80]))


def render_file(section, number, items):
    lines = [
        "--disable_abort_on_error",
        "--enable_warnings",
        "--echo # {} {}".format(section, number),
        SESSION + ";",
        "--echo errno $mysql_errno",
    ]
    statements = [stmt(SESSION)]
    for item in items:
        if isinstance(item, Statement):
            check_statement(item)
            lines.append(item.sql + ";")
            lines.append("--echo errno $mysql_errno")
            statements.append(item)
        else:
            lines.append(item)
    return Case("\n".join(lines) + "\n", statements)


def build_corpus():
    files = OrderedDict()
    for section, units in section_units().items():
        limit = {"s7_long": 10 ** 6, "s8_index": 1}.get(section, FILE_LIMIT)
        for number, items in enumerate(pack_units(units, limit), 1):
            name = "{}_{:04d}.test".format(section, number)
            if "." in name[: -len(".test")]:
                raise GeneratorError("a file name has a dot: {}".format(name))
            files[name] = render_file(section, number, items)
    return files


def summary(files):
    kinds = OrderedDict()
    errors = 0
    total = 0
    for case in files.values():
        for statement in case.statements:
            total += 1
            kinds[statement.kind] = kinds.get(statement.kind, 0) + 1
            if statement.errno:
                errors += 1
    parts = ", ".join("{} {}".format(count, kind) for kind, count in kinds.items())
    return "{} files, {} statements ({}), {} expected to fail".format(len(files), total, parts, errors)


def write_outputs(files, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(out_dir.glob("*.test")):
        if stale.name not in files:
            stale.unlink()
    for name, case in files.items():
        (out_dir / name).write_bytes(case.text.encode("utf-8"))


def check_outputs(files, out_dir):
    problems = []
    out_dir = Path(out_dir)
    for name, case in files.items():
        path = out_dir / name
        if not path.is_file():
            problems.append("missing: {}".format(path))
        elif path.read_bytes() != case.text.encode("utf-8"):
            problems.append("differs: {}".format(path))
    if out_dir.is_dir():
        for path in sorted(out_dir.iterdir()):
            if path.name not in files:
                problems.append("not generated: {}".format(path))
    return problems


def recorded_blocks(text, statements):
    lines = text.split("\n")
    position = 0
    blocks = []
    for statement in statements:
        echo = statement.sql + ";"
        found = None
        for index in range(position, len(lines)):
            if lines[index] == echo:
                found = index
                break
        block = None
        if found is not None:
            for index in range(found + 1, len(lines)):
                match = ERRNO_LINE.match(lines[index])
                if match:
                    block = (int(match.group(1)), lines[found + 1:index])
                    position = index + 1
                    break
        blocks.append(block)
    return blocks


def check_all_json(value):
    try:
        doc = json.loads(value)
    except ValueError:
        return "not JSON"
    if not isinstance(doc, dict) or list(doc.keys()) != ["tokens", "doc_len"]:
        return "not an object with tokens and doc_len"
    if not isinstance(doc["tokens"], list) or not isinstance(doc["doc_len"], int):
        return "tokens is not a list or doc_len is not a number"
    total = 0
    seen = set()
    for item in doc["tokens"]:
        if not isinstance(item, dict) or len(item) != 1:
            return "a token entry is not an object with one key"
        word, count = next(iter(item.items()))
        if not word or not isinstance(count, int) or count <= 0:
            return "a token is empty or its count is not positive"
        if word in seen:
            return "a token appears twice"
        seen.add(word)
        total += count
    if total != doc["doc_len"]:
        return "doc_len {} is not the sum of the counts {}".format(doc["doc_len"], total)
    return None


def check_default_json(value):
    try:
        doc = json.loads(value)
    except ValueError:
        return "not JSON"
    if not isinstance(doc, list):
        return "not an array"
    if any(not isinstance(word, str) or not word for word in doc):
        return "an entry is not a non-empty string"
    if len(set(doc)) != len(doc):
        return "a token appears twice"
    return None


def check_block(statement, lines):
    if statement.kind not in JSON_KINDS:
        return None
    if statement.kind in ROW_KINDS:
        rows = lines[1:]
        if len(rows) != statement.extra:
            return "{} rows, expected {}".format(len(rows), statement.extra)
        checker = check_all_json if statement.kind == "rows_all" else check_default_json
        for row in rows:
            problem = checker(row.split("\t")[-1])
            if problem:
                return problem
        return None
    if len(lines) != 2:
        return "{} lines after the echo, expected a header and one row".format(len(lines))
    if statement.kind == "long":
        fields = lines[1].split("\t")
        if len(fields) != 3:
            return "the row does not have three columns"
        if fields[1] != str(statement.extra):
            return "CHAR_LENGTH is {}, expected {}".format(fields[1], statement.extra)
        return check_all_json(fields[2])
    if statement.kind == "all":
        return check_all_json(lines[1])
    return check_default_json(lines[1])


def recording_findings(files, record_dir):
    problems = []
    counts = OrderedDict((("statements", 0), ("errors", 0), ("token_lists", 0)))
    record_dir = Path(record_dir)
    for name, case in files.items():
        stem = name[: -len(".test")]
        result = record_dir / (stem + ".result")
        if not result.is_file():
            partial = record_dir / (stem + ".partial")
            problems.append("{}: no recording{}".format(stem, " (a .partial log exists)" if partial.is_file() else ""))
            continue
        text = result.read_text(encoding="utf-8", errors="replace")
        for statement, block in zip(case.statements, recorded_blocks(text, case.statements)):
            shown = statement.sql if len(statement.sql) <= 160 else statement.sql[:157] + "..."
            counts["statements"] += 1
            if block is None:
                problems.append("{}: statement or its errno line not found: {}".format(stem, shown))
                continue
            errno, lines = block
            if CLIENT_ERRNO_FIRST <= errno <= CLIENT_ERRNO_LAST:
                problems.append("{}: client error {} (the connection to the server was lost): {}".format(stem, errno, shown))
            elif errno != statement.errno:
                problems.append("{}: errno {}, expected {}: {}".format(stem, errno, statement.errno, shown))
            elif errno:
                counts["errors"] += 1
            else:
                problem = check_block(statement, lines)
                if problem:
                    problems.append("{}: {}: {}".format(stem, problem, shown))
                elif statement.kind in JSON_KINDS:
                    counts["token_lists"] += 1
    return problems, counts


def token_counts(kind, lines):
    rows = lines[1:]
    counts = []
    for row in rows:
        value = row.split("\t")[-1]
        try:
            doc = json.loads(value)
        except ValueError:
            return None
        if kind in ("all", "long", "rows_all"):
            if not isinstance(doc, dict) or not isinstance(doc.get("tokens"), list):
                return None
            words = OrderedDict()
            for item in doc["tokens"]:
                if not isinstance(item, dict) or len(item) != 1:
                    return None
                word, count = next(iter(item.items()))
                words[word] = count
            counts.append((tuple(row.split("\t")[:-1]), doc.get("doc_len"), words))
        else:
            if not isinstance(doc, list):
                return None
            counts.append((tuple(row.split("\t")[:-1]), None, OrderedDict((word, 1) for word in doc)))
    return counts


def describe_token_change(left, right):
    parts = []
    if len(left) != len(right):
        return "{} rows on the left, {} on the right".format(len(left), len(right))
    for (left_key, left_len, left_words), (right_key, right_len, right_words) in zip(left, right):
        if left_key != right_key:
            parts.append("row {} became {}".format("\t".join(left_key), "\t".join(right_key)))
            continue
        only_left = [word for word in left_words if word not in right_words]
        only_right = [word for word in right_words if word not in left_words]
        changed = [word for word in left_words if word in right_words and left_words[word] != right_words[word]]
        if only_left:
            parts.append("only left: " + " ".join(only_left[:12]) + (" ..." if len(only_left) > 12 else ""))
        if only_right:
            parts.append("only right: " + " ".join(only_right[:12]) + (" ..." if len(only_right) > 12 else ""))
        if changed:
            parts.append("count changed: " + " ".join(
                "{} {}->{}".format(word, left_words[word], right_words[word]) for word in changed[:12]))
        if left_len != right_len:
            parts.append("doc_len {}->{}".format(left_len, right_len))
    return "; ".join(parts)


def token_differences(files, left_dir, right_dir):
    lines_out = []
    counts = OrderedDict((("identical", 0), ("order_only", 0), ("tokens_differ", 0), ("other", 0), ("missing", 0)))
    for name, case in files.items():
        stem = name[: -len(".test")]
        texts = []
        for directory in (left_dir, right_dir):
            path = Path(directory) / (stem + ".result")
            texts.append(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None)
        if texts[0] is None or texts[1] is None:
            counts["missing"] += 1
            lines_out.append("{}: missing on the {}".format(stem, "left" if texts[0] is None else "right"))
            continue
        left_blocks = recorded_blocks(texts[0], case.statements)
        right_blocks = recorded_blocks(texts[1], case.statements)
        for statement, left, right in zip(case.statements, left_blocks, right_blocks):
            shown = statement.sql if len(statement.sql) <= 120 else statement.sql[:117] + "..."
            if left == right:
                counts["identical"] += 1
                continue
            if left is None or right is None:
                counts["missing"] += 1
                lines_out.append("{}: not found on the {}: {}".format(stem, "left" if left is None else "right", shown))
                continue
            if left[0] == 0 and right[0] == 0 and statement.kind in JSON_KINDS:
                left_counts = token_counts(statement.kind, left[1])
                right_counts = token_counts(statement.kind, right[1])
                if left_counts is not None and right_counts is not None:
                    if [(k, n, dict(w)) for k, n, w in left_counts] == [(k, n, dict(w)) for k, n, w in right_counts]:
                        counts["order_only"] += 1
                        lines_out.append("{}: same tokens, other order: {}".format(stem, shown))
                    else:
                        counts["tokens_differ"] += 1
                        lines_out.append("{}: tokens differ ({}): {}".format(
                            stem, describe_token_change(left_counts, right_counts), shown))
                    continue
            counts["other"] += 1
            lines_out.append("{}: errno {} -> {}, output differs: {}".format(stem, left[0], right[0], shown))
    return lines_out, counts


def command_diff_tokens(args):
    files = build_corpus()
    lines_out, counts = token_differences(files, args.left, args.right)
    for line in lines_out:
        print(line)
    print(", ".join("{} {}".format(value, key) for key, value in counts.items()))
    return 0 if sum(value for key, value in counts.items() if key != "identical") == 0 else 1


def command_cases(args):
    files = build_corpus()
    if args.check:
        problems = check_outputs(files, args.out)
        for problem in problems:
            print(problem)
        print(summary(files))
        return 1 if problems else 0
    write_outputs(files, args.out)
    print(summary(files))
    return 0


def command_check_recording(args):
    files = build_corpus()
    problems, counts = recording_findings(files, args.record_dir)
    for line in problems:
        print(line)
    print(
        "{} files checked: {} statements, {} failed as expected, {} token lists well formed, {} problems".format(
            len(files), counts["statements"], counts["errors"], counts["token_lists"], len(problems)
        )
    )
    return 1 if problems else 0


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command")
    cases = subparsers.add_parser("cases", help="write the .test files")
    cases.add_argument("--out", default=str(DEFAULT_OUT), help="directory for the .test files")
    cases.add_argument("--check", action="store_true", help="write nothing; exit 1 if the files differ")
    check = subparsers.add_parser("check-recording", help="check a recording of the corpus")
    check.add_argument("--record-dir", required=True, help="the runner's --record-dir of one recording")
    diff = subparsers.add_parser(
        "diff-tokens",
        help="explain the differences between two recordings statement by statement; the verdict stays compare's",
    )
    diff.add_argument("--left", required=True, help="the --record-dir of one recording")
    diff.add_argument("--right", required=True, help="the --record-dir of the other recording")
    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "cases":
            return command_cases(args)
        if args.command == "check-recording":
            return command_check_recording(args)
        if args.command == "diff-tokens":
            return command_diff_tokens(args)
    except GeneratorError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
