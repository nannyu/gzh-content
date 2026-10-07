# Spectre VPN 分流规则

版本：2026.10.07.1。面向 iOS Spectre VPN 的规则网址入口，规则文件不含服务器、订阅凭据或控制密钥。

## 导入网址

首选 GitHub Raw：

https://raw.githubusercontent.com/nannyu/gzh-content/stash-routing/configs/spectre-blacklist-routing.conf

备用 jsDelivr（可能存在缓存延迟）：

https://cdn.jsdelivr.net/gh/nannyu/gzh-content@stash-routing/configs/spectre-blacklist-routing.conf

在 Spectre 切换到 **规则 / Rule 模式**，打开规则设置，把上述地址添加到**规则网址**，下载并选用该网址的规则，然后重新连接。界面名称可能随版本变化。这是规则列表，不能当成服务器订阅，也不是高级自定义 sing-box JSON。

## 分流行为

顺序沿用 Stash 版：局域网 → 指定国内应用优先直连 → 显式服务规则 → 服务规则集 → 私网 → 广告 → 国内域名 → 代理黑名单 → 中国 IP → 默认直连。

- 微信、微信支付、小红书、高德、哔哩哔哩、知乎、支付宝、美团、主流国内银行和银联：保留全部 176 条优先直连规则。
- 海外服务的 Stash 自定义策略组统一转换为 `PROXY`，使用 Spectre 当前选择的服务器，不提供自动优选或按服务选不同节点。
- 广告策略固定为 `REJECT`，未匹配流量固定为 `FINAL,DIRECT`，对应原 Stash 版的默认组选择。此文件不迁移手机上的临时策略组选择状态。
- 13 个远程规则集已展开为静态规则，精确重复项去重；共 139,906 条，4,739,918 字节（约 4.7 MB）。首次导入可能需要更长时间。
- 不加入 DNS、节点、证书、HTTPS 解密或系统代理设置。

## 适配差异与验证边界

采用 `[Rule]` 文本列表，仅输出 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、`IP-CIDR`、`IP-CIDR6`、`GEOIP` 和 `FINAL`；动作仅 `DIRECT`、`PROXY`、`REJECT`。

- Clash 的 `+.example.com` 转换为 `DOMAIN-SUFFIX,example.com`，普通域名转换为 `DOMAIN`，保留精确/后缀区别。
- 去掉 `no-resolve` 参数以使用基础三字段文本形式；客户端进行 IP 规则判断时，DNS 解析时机可能与 Stash 不同。
- 不输出上游 Spotify 和 Netflix 的两条 Android `PROCESS-NAME` 规则。
- 不输出 OpenAI 集合的一条 `IP-ASN,20473`。其域名、IP 规则仍保留，但这个 ASN 内未匹配其他规则的请求不保证走代理。
- 使用本次下载的上游固定提交快照，不会像 Stash 的 rule-providers 那样单独后台更新。更新后需重新生成并在 Spectre 下载规则。
- 没有域名的连接依据 IP 规则处理。银行或 App 新增域名、第三方服务仍可能需要补充。

**已完成静态转换与检查，尚未在 Spectre 真机导入或验证连接。** 当前官方说明确认规则模式和从网址导入规则列表，但未提供完整文本解析器规范，因此静态检查不等于 Spectre 客户端兼容性认证。如出现导入错误，需要根据具体 App 版本与错误定位，不应启用全局代理代替分流验证。

## 维护与复现

- `stash-blacklist-routing.stoverride`：显式规则和策略的源文件。
- `spectre-sources.json`：13 个上游文件的固定提交 URL、SHA-256、规则数量。
- `spectre-blacklist-routing.conf`：生成的导入文件。
- `spectre-validation.json`：输出哈希、规则数、未输出条目及验证范围。
- `../scripts/build_spectre_rules.py`：生成器；依赖 Python 3、PyYAML、curl。

```sh
python3 scripts/build_spectre_rules.py
python3 scripts/build_spectre_rules.py --check
```

生成器验证上游 SHA-256，未知语法会报错，不会静默丢弃。更新上游快照需显式更新来源锁定文件中的提交地址、哈希及条目数，再重新生成并检查。

本次检查包括：全部输出语法、IPv4/IPv6 CIDR、352 个国内应用根域名/子域名直连样例、8 个海外服务代理样例、唯一的最终直连规则、确定性重建与文件哈希。

## 参考

- [Spectre 官方支持页](https://proxy.spectreapp.xyz/support/)：规则模式与自定义 sing-box 配置的区别。
- [开发者 App Store 说明](https://apps.apple.com/ca/app/spectre-vpn/id1508712998)：支持编辑规则、从 URL 导入规则列表。
- [pexcn/daily 文本配置样例](https://github.com/pexcn/daily/blob/gh-pages/shadowrocket/whitelist.conf)：`[Rule]` / `FINAL` 通用文本格式参考；不是 Spectre 原生解析器验证。
- 上游数据：`blackmatrix7/ios_rule_script`、`Loyalsoldier/clash-rules`、`TG-Twilight/AWAvenue-Ads-Rule`；具体文件与固定提交见 `spectre-sources.json`。
