# Web 手机验证码注册内核

本目录是多用户 Web 版批次 1 的确定性安全内核，不调用大模型。它已经实现并测试：

- 中国大陆手机号规范化；
- 6 位随机验证码、5 分钟有效、HMAC 存储、错误次数锁定和单次消费；
- 新验证码使旧验证码失效；
- 手机号、IP、设备和全局预算的原子限流参考实现；
- 风险升高后要求服务端验证的一次性 CAPTCHA；
- 短信供应商失败的安全终态；
- 未成年人必须提交经服务端验证的监护同意凭据；
- 会话令牌只在成功响应中返回，服务端仅保存哈希；
- 手机号、IP、设备和验证码不以明文写入审计。
- `POST /v1/auth/otp/request` 与 `POST /v1/auth/otp/verify` 的 ASGI 接口适配器；
- HTTPS/Host/JSON/请求体边界、安全 Cookie、统一错误响应，并明确不信任客户端 `X-Forwarded-For`。

`registration.py` 不依赖 Web 框架或数据库驱动，`asgi.py` 只实现最小 ASGI 协议，`mysql_store.py` 接受任意 PyMySQL 兼容的连接工厂。生产 HTTP API 应把真实短信、CAPTCHA、监护同意和 MySQL 连接工厂注入 `RegistrationService`，再交给支持 ASGI 的生产服务器；不得使用 `RecordingSmsSender`、`InMemoryCaptchaVerifier`、`InMemoryGuardianConsentVerifier` 或 `InMemoryRegistrationStore`。数据库骨架见 `migrations/0001_phone_registration.sql`。

生产接入仍须完成 `docs/web/05-TEST-ACCEPTANCE-OPERATIONS.md` 中的真实 MySQL 并发、供应商回执、枚举时序、预算熔断、监控和灰度验收。当前脚本化测试只证明事务 SQL 的锁定顺序和提交/回滚路径，不能替代 RDS 集成测试。模型只能离线审查代码，不能实时决定是否发送验证码、是否登录或是否授予会话。
