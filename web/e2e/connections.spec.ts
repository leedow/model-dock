import { test, expect } from "@playwright/test";

test("encrypted connections, shared models, key updates and both chat modes", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "模型服务，尽在掌握。" }),
  ).toBeVisible();
  await page.goto("/providers");
  await page.getByRole("button", { name: "添加厂商连接", exact: true }).click();
  await page
    .getByLabel("名称", { exact: true })
    .fill("browser-chat-connection");
  await page
    .getByLabel("服务地址", { exact: true })
    .fill("http://provider.test");
  await page
    .getByLabel("厂商 API Key", { exact: true })
    .fill("browser-connection-secret");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const row = page
    .getByRole("row")
    .filter({ hasText: "browser-chat-connection" });
  await expect(row).toContainText("已配置");
  await row.getByRole("button", { name: "编辑厂商连接", exact: true }).click();
  await expect(page.getByLabel("厂商 API Key", { exact: true })).toHaveValue(
    "",
  );
  await page
    .getByLabel("厂商 API Key", { exact: true })
    .fill("browser-rotated-secret");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  const auth = await (await page.request.get("/api/v1/auth/me")).json();
  const headers = { "X-CSRF-Token": auth.csrf_token };
  const response = await page.request.get("/api/v1/provider-connections");
  expect(await response.text()).not.toContain("browser-rotated-secret");
  expect(await response.text()).not.toContain("encrypted_api_key");
  const connection = (await response.json()).data.find(
    (c: { name: string }) => c.name === "browser-chat-connection",
  );
  for (const name of ["connected-llm-one", "connected-llm-two"]) {
    await page.goto("/models");
    await page.getByRole("button", { name: "添加模型", exact: true }).click();
    await page.getByLabel("名称", { exact: true }).fill(name);
    await page.getByRole("combobox", { name: "厂商连接", exact: true }).click();
    await page
      .getByText("browser-chat-connection", { exact: true })
      .last()
      .click();
    await page.getByLabel("上游模型 ID", { exact: true }).fill("account-model");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: name })).toContainText(
      "已停用",
    );
  }
  const models = (await (await page.request.get("/api/v1/models")).json()).data;
  const model = models.find(
    (m: { name: string }) => m.name === "connected-llm-one",
  );
  await page.request.post(`/api/v1/models/${model.id}/enable`, { headers });
  await page.goto("/playground?model=connected-llm-one");
  await expect(
    page.getByRole("switch", { name: "使用厂商默认采样参数" }),
  ).toBeChecked();
  await page.getByRole("textbox", { name: "输入内容" }).fill("hello stream");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("Fake provider answer", { exact: true }),
  ).toBeVisible();
  await page.getByRole("switch", { name: "流式输出", exact: true }).click();
  await page.getByRole("textbox", { name: "输入内容" }).fill("hello nonstream");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("Fake provider answer", { exact: true }),
  ).toHaveCount(2);
  await page.goto("/providers");
  await expect(row).toContainText("connected-llm-one");
  await expect(row).toContainText("connected-llm-two");
  await expect(
    row.getByRole("button", { name: "删除", exact: true }),
  ).toBeDisabled();
  await row.getByRole("button", { name: "编辑厂商连接", exact: true }).click();
  await page.getByLabel("厂商 API Key", { exact: true }).fill("another-secret");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(
    page.getByText("请先卸载全部关联模型，并等待排队、运行和加载操作结束。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByLabel("厂商 API Key", { exact: true })).toHaveValue(
    "",
  );
  const storage = await page.evaluate(() =>
    JSON.stringify({ local: localStorage, session: sessionStorage }),
  );
  expect(storage).not.toContain("secret");
  const current = (
    await (await page.request.get("/api/v1/provider-connections")).json()
  ).data.find((c: { id: string }) => c.id === connection.id);
  expect(current.models).toHaveLength(2);
});
