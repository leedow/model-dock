import { test, expect } from "@playwright/test";

test("speech model downloads, registration, local playback and cancellation", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await page.getByRole("link", { name: "模型管理", exact: true }).click();
  await page.getByRole("button", { name: "主模型下载", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "主模型下载" });
  const kokoro = drawer
    .locator(".ant-card")
    .filter({ hasText: "Kokoro Chinese 1.1" });
  await kokoro.getByRole("button", { name: "下载模型", exact: true }).click();
  await expect(drawer.getByText("已完成", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "创建模型配置" }).click();
  await page.keyboard.press("Escape");
  await page.getByPlaceholder("搜索模型名称…").fill("local-kokoro");
  const row = page.getByRole("row").filter({ hasText: "local-kokoro" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "启用", exact: true }).click();
  await page.getByRole("button", { name: /确\s*定/ }).click();
  await expect(
    row.getByRole("button", { name: "停用", exact: true }),
  ).toBeVisible();
  await page.goto("/playground?model=local-kokoro");
  await expect(page.getByText(/本地 CPU 语音模型/)).toBeVisible();
  await page.getByLabel("合成文本", { exact: true }).fill("浏览器语音夹具测试");
  await page.getByRole("button", { name: "提交任务", exact: true }).click();
  await expect(page.locator(".file-preview audio")).toBeVisible();
  await expect(page.getByText("示例提示音，并非真实语音合成")).toHaveCount(0);
  await page.getByRole("link", { name: "模型管理", exact: true }).click();
  await page.getByRole("button", { name: "主模型下载", exact: true }).click();
  await drawer
    .locator(".ant-card")
    .filter({ hasText: "Paraformer Chinese streaming" })
    .getByRole("button", { name: "下载模型", exact: true })
    .click();
  const running = drawer
    .locator(".ant-card")
    .filter({ hasText: "Paraformer Chinese streaming" })
    .filter({ has: page.getByRole("button", { name: "取消", exact: true }) });
  await expect(running).toBeVisible();
  await running.getByRole("button", { name: "取消", exact: true }).click();
  await expect(drawer.getByText("已取消", { exact: true })).toBeVisible();
  await expect(
    drawer.getByRole("button", { name: "重试", exact: true }),
  ).toBeVisible();
});
