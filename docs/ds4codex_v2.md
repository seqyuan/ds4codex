# 不改 Codex，只加一个包：把 DeepSeek V4 接进来的更顺手方案

原来的思路是对的: 在本地加一层代理，把 Codex 的 Responses API 翻译成 DeepSeek 能听懂的 Chat Completions。

问题不在方案本身，而在交付形态还停留在「一段脚本 + 一条 `export` + 一份手工说明」。

这在自己机器上能跑，但一旦想复用、交接、发给同事，成本马上就上来了:

- 脚本放哪儿不统一
- 参数靠手改
- `export DEEPSEEK_API_KEY=...` 容易忘
- 文档和代码容易漂移
- 每次都要解释 `config.toml` 怎么写

V2 的方向不是换技术，而是把它产品化成一个真正可安装的命令行包。

---

## 一、V2 版应该长什么样

最顺手的使用体验应该是这样:

```bash
pip install ds4codex
ds4codex init
ds4codex codex-config
ds4codex run
```

也就是说，用户不需要再去复制 `responses_proxy.py` 到某个隐藏目录，也不需要自己维护一个 `proxy.sh`。

包安装完，CLI 直接可用。

---

## 二、为什么要从脚本升级成 Python 包

脚本适合验证思路，包适合长期使用。

把它做成 Python 包以后，至少有四个直接收益:

1. 安装方式统一

用户只需要 `pip install .`、`pip install ds4codex` 或 `pipx install ds4codex`，不需要再手动摆放脚本。

2. 命令入口统一

不再是：

```bash
python3 ~/.codex/responses_proxy.py --port 8099
```

而是：

```bash
ds4codex run
```

3. 配置位置统一

不再把配置写进安装目录，也不依赖用户记住一堆环境变量。稳定配置放到：

```text
~/.config/ds4codex/config.toml
```

4. 文档和实现绑定

CLI、默认配置、README、软文文档可以一起演进，不容易出现“文档还是旧的，代码已经改了”的情况。

---

## 三、`export` 不是不能用，但不该再是唯一做法

原文档里的做法是：

```bash
export DEEPSEEK_API_KEY="sk-..."
```

这个方式最大的问题不是错，而是太脆弱:

- 只在当前 shell 会话有效
- 换终端就没了
- 放到 `~/.bashrc` 又容易把临时变量变成长期变量
- 同一台机器切换多个 provider 时管理混乱

更合适的做法应该分层:

### 方案 A：推荐，Key 只存一份

直接把真实上游 key 放在 `~/.codex/config.toml` 的 provider 配置里。

Codex 请求本地代理时会带上 `Authorization: Bearer ...`，`ds4codex` 再把这个 token 转发给上游。

优点很明显:

- 不需要重复维护两份 key
- 不需要每次 `export`
- `ds4codex` 自己的配置文件可以保持无密钥

### 方案 B：适合服务化运行

如果不想把真实 key 放到 Codex 配置里，就让 `ds4codex` 从环境变量或自身配置读取:

```bash
export DEEPSEEK_API_KEY="sk-..."
ds4codex run
```

或者写进：

```toml
[proxy]
api_key_env = "DEEPSEEK_API_KEY"
```

这样也行，但它现在只是“可选路径”，不再是唯一入口。

### 方案 C：临时调试

直接命令行传入:

```bash
ds4codex run --api-key sk-...
```

适合临时排障，不适合长期保留。

---

## 四、V2 版 CLI 应该提供什么

参考 `midfile` 的结构，最合适的是保留一个明确的 Click CLI 入口，把职责拆干净:

- `ds4codex init`
  生成默认配置文件

- `ds4codex info`
  查看当前解析后的配置、端口、目标地址、鉴权来源

- `ds4codex codex-config`
  自动打印一段可直接粘贴到 `~/.codex/config.toml` 的配置

- `ds4codex run`
  启动代理服务

这比“文末贴完整脚本 + 再贴一个 shell 脚本”更像一个可维护产品。

---

## 五、为什么这次更适合做 Python 包，而不是 npm 包

如果只看“能不能做”，两边都能做。

但如果看“这次最合适的落地方式”，Python 更优。

原因很直接:

1. 现有实现已经是 Python

代理逻辑、请求翻译、SSE 转发都已经在 Python 里验证过了，直接包化成本最低。

2. 这是一个后端小工具，不是前端产品

它的核心就是 HTTP 转发和协议翻译，不依赖浏览器生态，也不需要 Node 的前端工具链优势。

3. 目标用户大概率已经接受 Python CLI

能跑 Codex、本地脚本、代理服务的用户，一般装 `pip` 或 `pipx` 没什么障碍。

4. npm 会引入一次没必要的重写

如果改成 npm，本质上是在为“分发形式”重写运行时，而不是在增强产品能力。

Node 什么时候更合理？

- 你要把它嵌进现有 Node 服务
- 你准备做 VS Code 扩展或 Electron 客户端
- 你的团队运维规范就是统一发 npm 包

但在当前阶段，先把 Python 包做好，才是最短路径。

---

## 六、V2 版的实际交付应该包含什么

这次更像一个最小可用产品，而不是一篇脚本教程。

建议交付件至少包括:

- `pyproject.toml`
- `ds4codex/__init__.py`
- `ds4codex/cli.py`
- `ds4codex/config.py`
- `ds4codex/proxy.py`
- `ds4codex/default_config.toml`
- `README.md`
- `docs/ds4codex_v2.md`

这样技术说明、安装入口、CLI、默认配置和传播文案是同一个版本线。

---

## 七、最终结论

这件事适合继续做，而且应该直接做成 Python 包。

重点不是把脚本“塞进包里”，而是顺手把三件事情一起做对:

- CLI 入口标准化
- 配置从 `export` 升级成“配置文件为主，环境变量和命令行覆盖”
- 文档从“技术脚本贴”升级成“可复用的产品说明”

如果后面真的有 Node 生态需求，再补一个 npm 包壳都来得及。

但第一版正式形态，Python 应该是主包，不建议先走 npm。
