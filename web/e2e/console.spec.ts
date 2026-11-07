import { test, expect } from "@playwright/test";
import { writeFileSync, mkdirSync } from "node:fs";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "模型服务，尽在掌握。" }),
  ).toBeVisible();
}

test("complete console: models, chat, media, tasks, keys, users, docs, language and theme", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  await expect(page.getByText("mock-chat", { exact: true })).toBeVisible();
  await expect(page.locator(".fleet-card .ant-spin-spinning")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/overview.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("link", { name: "模型管理", exact: true }).click();
  await expect(page.getByText("mock-chat", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "添加模型", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("browser-chat");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("browser-chat", { exact: true })).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: "browser-chat" });
  await row.getByRole("button", { name: "加载", exact: true }).click();
  await expect(row.getByText("已就绪")).toBeVisible();
  await row.getByRole("button", { name: "卸载", exact: true }).click();
  await expect(row.getByText("未加载")).toBeVisible();
  await page.getByRole("link", { name: "在线测试", exact: true }).click();
  await page.getByRole("textbox", { name: "输入内容" }).fill("浏览器测试");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".chat-message.assistant")).toContainText(
    "浏览器测试",
  );
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /图像生成/ }).click();
  await page.getByRole("textbox", { name: "输入内容" }).fill("a purple planet");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".file-preview img")).toBeVisible();
  await page.getByRole("button", { name: /语音合成/ }).click();
  await page.getByRole("textbox", { name: "输入内容" }).fill("hello");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".file-preview audio")).toBeVisible();
  const wav = await page.locator(".file-preview audio").getAttribute("src");
  const response = await page.request.get(wav!);
  mkdirSync("test-results", { recursive: true });
  writeFileSync("test-results/sample.wav", await response.body());
  await page.getByRole("button", { name: /语音识别/ }).click();
  await page
    .locator("input[type=file]")
    .setInputFiles("test-results/sample.wav");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".transcript")).toContainText("模拟语音转写");
  await page.getByRole("button", { name: /图像生成/ }).click();
  await page.getByRole("switch").click();
  await page.getByRole("textbox", { name: "输入内容" }).fill("async planet");
  await page.getByRole("button", { name: "提交任务", exact: true }).click();
  await expect(page.locator(".task-submitted")).toBeVisible();
  await page
    .getByRole("link", { name: "任务中心", exact: true })
    .first()
    .click();
  await expect(page.getByText("已完成").first()).toBeVisible();
  await page.getByRole("button", { name: "详情", exact: true }).first().click();
  await expect(page.getByText("任务结果", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("link", { name: "文件管理", exact: true }).click();
  await expect(page.getByText("mock-image-1.png").first()).toBeVisible();
  await page.getByRole("link", { name: "API Key", exact: true }).click();
  await page.getByRole("button", { name: "创建 API Key", exact: true }).click();
  await page.getByLabel("Key 名称").fill("browser-key");
  await page.getByRole("dialog").getByLabel("模型权限").click();
  await page.getByText("mock-chat", { exact: true }).last().click();
  await page.keyboard.press("Escape");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /确\s*定/ })
    .click();
  await expect(page.getByText("请立即保存你的 API Key")).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await page.getByRole("button", { name: /确\s*定/ }).click();
  await expect(page.getByText("已撤销", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "用户管理", exact: true }).click();
  await page.getByRole("button", { name: "添加用户", exact: true }).click();
  await page.getByLabel("用户名", { exact: true }).fill("browser-user");
  await page.getByLabel("密码", { exact: true }).fill("browser-user-password");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /确\s*定/ })
    .click();
  await expect(page.getByText("browser-user", { exact: true })).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: "browser-user" })
    .getByRole("button", { name: "配置模型授权" })
    .click();
  await page.getByRole("dialog").getByLabel("模型权限").click();
  await page.getByText("mock-chat", { exact: true }).last().click();
  await page.keyboard.press("Escape");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /确\s*定/ })
    .click();
  await expect(
    page.getByRole("row").filter({ hasText: "browser-user" }),
  ).toContainText("mock-chat");
  await page.getByRole("link", { name: "调用日志", exact: true }).click();
  await expect(page.getByRole("table")).toContainText("mock-chat");
  await page.getByRole("link", { name: "接入文档", exact: true }).click();
  await expect(page.getByText("Python SDK", { exact: true })).toBeVisible();
  const language = page.getByRole("combobox", { name: "语言" });
  await language.focus();
  await language.press("ArrowDown");
  await page.getByText("EN", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "A few lines. All your models." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Appearance" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(
    page.getByRole("heading", { name: "A few lines. All your models." }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/docs-dark.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("mobile navigation and responsive login", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page
    .getByRole("button")
    .filter({ has: page.locator(".anticon-menu") })
    .click();
  await page.getByRole("link", { name: "在线测试", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "在线测试", exact: true }),
  ).toBeVisible();
  await expect
    .poll(async () =>
      page
        .locator(".sidebar")
        .evaluate((el) => Math.round(el.getBoundingClientRect().right)),
    )
    .toBeLessThanOrEqual(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "test-results/mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
