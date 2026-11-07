import { test, expect } from "@playwright/test";

test("provider configuration, media operations, cancellation and retry", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "模型服务，尽在掌握。" }),
  ).toBeVisible();
  const auth = await (await page.request.get("/api/v1/auth/me")).json();
  const headers = { "X-CSRF-Token": auth.csrf_token };
  const create = async (
    name: string,
    capability: string,
    adapter: string,
    config: object,
  ) => {
    const response = await page.request.post("/api/v1/models", {
      headers,
      data: { name, capability, adapter, enabled: true, config },
    });
    expect(response.status(), await response.text()).toBe(201);
  };
  // Exercise the actual configuration editor and schema before API-assisted setup.
  await page.goto("/models");
  await page.getByRole("button", { name: "添加模型", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("browser-provider-draft");
  await page.getByLabel("适配器", { exact: true }).press("ArrowDown");
  await page
    .getByText("openai_compatible.chat", { exact: true })
    .last()
    .click();
  await expect(
    page.getByText("配置 JSON Schema", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(
    page.getByRole("row").filter({ hasText: "browser-provider-draft" }),
  ).toContainText("已停用");
  const base = {
    base_url: "http://provider.test",
    api_key_env: "MODELDock_BROWSER_KEY",
    poll_interval: 0.02,
  };
  await create("browser-remote-image", "image", "runninghub.image", {
    ...base,
    endpoints: { generate: "image/generate", edit: "image/edit" },
  });
  await create("browser-remote-video", "video", "runninghub.video", {
    ...base,
    endpoints: { image_to_video: "video/generate" },
    resolutions: ["720p"],
  });
  await create("browser-remote-tts", "tts", "volcengine.tts", {
    ...base,
    app_id_env: "MODELDock_BROWSER_APP",
    resource_id: "fake-resource",
    voices: ["fake-voice"],
    formats: ["wav"],
  });
  await create(
    "browser-remote-subtitle",
    "subtitle",
    "whisperx.subtitle",
    base,
  );
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGNsYGhgwAaYsIoOWgkAzQgBEJwaof4AAAAASUVORK5CYII=",
    "base64",
  );
  const image = await page.request.post("/v1/files", {
    headers,
    multipart: {
      file: { name: "reference.png", mimeType: "image/png", buffer: png },
    },
  });
  expect(image.status(), await image.text()).toBe(201);
  const uploaded = await image.json();
  const open = async (name: string) => {
    await page.goto("/playground?model=" + name);
    await expect(page.locator("form[id^=remote-playground]")).toBeVisible();
  };
  await open("browser-remote-image");
  await page.getByLabel("输入内容", { exact: true }).fill("a planet");
  await page.locator("form[id^=remote-playground] button[type=submit]").click();
  await expect(page.locator(".file-preview img")).toBeVisible();
  await page.getByLabel("操作类型", { exact: true }).press("ArrowDown");
  await page.getByText("图像编辑", { exact: true }).last().click();
  await page
    .getByRole("combobox", { name: "输入图片", exact: true })
    .press("ArrowDown");
  await page.getByText("reference.png", { exact: true }).last().click();
  await page.locator("form[id^=remote-playground] button[type=submit]").click();
  await expect(page.locator(".file-preview img")).toBeVisible();
  await open("browser-remote-video");
  await page
    .getByRole("combobox", { name: "输入图片", exact: true })
    .press("ArrowDown");
  await page.getByText("reference.png", { exact: true }).last().click();
  await page.getByLabel("输入内容", { exact: true }).fill("slow");
  await page.locator("form[id^=remote-playground] button[type=submit]").click();
  await expect(
    page.getByRole("button", { name: "取消", exact: true }),
  ).toBeVisible();
  await expect(
    page.locator("pre").filter({ hasText: "upstream_task_id" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "重试", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("本地停止等待不代表上游已停止执行或计费，请查看取消状态。"),
  ).toBeVisible();
  await page.getByLabel("输入内容", { exact: true }).fill("fail-once");
  await page.locator("form[id^=remote-playground] button[type=submit]").click();
  await expect(
    page.getByRole("button", { name: "重试", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.locator(".file-preview video")).toBeVisible();
  const videoUrl = await page
    .locator(".file-preview video")
    .getAttribute("src");
  const partial = await page.request.get(videoUrl!, {
    headers: { Range: "bytes=0-15" },
  });
  expect(partial.status()).toBe(206);
  expect((await partial.body()).length).toBe(16);
  await open("browser-remote-tts");
  await page.getByLabel("语音合成文本", { exact: true }).fill("hello");
  await page.locator("form[id^=remote-playground] button[type=submit]").click();
  await expect(page.locator(".file-preview audio")).toBeVisible();
  const wavUrl = await page.locator(".file-preview audio").getAttribute("src");
  const wav = await (await page.request.get(wavUrl!)).body();
  await open("browser-remote-subtitle");
  await page
    .locator("input[type=file]")
    .first()
    .setInputFiles({ name: "voice.wav", mimeType: "audio/wav", buffer: wav });
  await expect(
    page.locator(".ant-select-selection-item[title='voice.wav']"),
  ).toBeVisible();
  await page.getByLabel("字幕原文", { exact: true }).fill("hello");
  await page.locator("form[id^=remote-playground] button[type=submit]").click();
  await expect(page.locator(".file-preview pre")).toContainText("[Events]");
  await expect(page.locator(".file-preview a")).toHaveAttribute(
    "download",
    "subtitle.ass",
  );
  const workflow = (
    operation: string,
    fields: string[],
    runninghub = false,
  ) => ({
    ...base,
    operation,
    ...(runninghub
      ? { workflow_id: "fake-workflow" }
      : {
          workflow: {
            "1": {
              class_type: "FakeInput",
              inputs: Object.fromEntries(fields.map((key) => [key, ""])),
            },
            "2": { class_type: "FakeOutput", inputs: {} },
          },
        }),
    bindings: Object.fromEntries(
      fields.map((key) => [key, { node_id: "1", field: key }]),
    ),
    output_nodes: ["2"],
  });
  await create(
    "browser-start-end",
    "video",
    "comfyui.video",
    workflow("start_end", ["image_file_id", "end_image_file_id"]),
  );
  await create(
    "browser-lipsync",
    "video",
    "runninghub.workflow.video",
    workflow("lip_sync", ["image_file_id", "audio_file_id"], true),
  );
  await create(
    "browser-upscale",
    "video",
    "comfyui.video",
    workflow("upscale", ["video_file_id", "scale"]),
  );
  const selectFile = async (label: string, filename: string) => {
    await page
      .getByRole("combobox", { name: label, exact: true })
      .press("ArrowDown");
    await page.getByText(filename, { exact: true }).last().click();
  };
  for (const name of [
    "browser-start-end",
    "browser-lipsync",
    "browser-upscale",
  ]) {
    await open(name);
    if (name === "browser-upscale") await selectFile("输入视频", "video.mp4");
    else {
      await selectFile("输入图片", "reference.png");
      if (name === "browser-start-end")
        await selectFile("尾帧图片", "reference.png");
      else await selectFile("输入音频", "voice.wav");
    }
    await page
      .locator("form[id^=remote-playground] button[type=submit]")
      .click();
    await expect(page.locator(".file-preview video")).toBeVisible();
  }
  await expect(page.locator(".file-preview video")).toHaveJSProperty(
    "readyState",
    4,
  );
  await page.screenshot({
    path: "/tmp/modeldock-provider-workbench.png",
    fullPage: true,
  });
  // Check language switching with the new capability labels.
  await page.getByRole("combobox", { name: "语言" }).press("ArrowDown");
  await page.getByText("EN", { exact: true }).last().click();
  await expect(page.getByRole("button", { name: /Subtitles/ })).toBeVisible();
  expect(uploaded.media_type).toBe("image/png");
});
