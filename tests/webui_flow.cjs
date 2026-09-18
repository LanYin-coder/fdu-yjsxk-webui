/* Local-only browser regression. NODE_PATH must provide Playwright.
   Start an isolated WebUI on port 18878. All APIs below are mocked. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const origin = process.env.FDU_TEST_URL || "http://127.0.0.1:18878";
const output = process.env.FDU_TEST_OUTPUT || path.join(require("node:os").tmpdir(), "fdu-webui-screenshots");
const localTime = value => {
  const d = new Date(value), pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
const initialConfig = {target:"yjsxk.fudan.edu.cn", start_time:localTime(Date.now()+600000),
  end_time:localTime(Date.now()+3600000), cookie_source:"file", browser:"edge", request_interval:.8,
  poll_interval:.6, poll_max:15, cookie_refresh_secs:240, http_timeout:12, serial_mode:false, full_max_tries:0, courses:[]};
const titles = ["新时代中国特色社会主义理论与实践", "学术英语", "机器学习理论", "社会科学研究方法", "中国马克思主义与当代", "现代统计分析"];
const catalog = Array.from({length:12}, (_,i) => ({name:titles[i%titles.length], kcdm:"TEST"+i,
  bjdm:"2026202701TEST."+String(i+1).padStart(2,"0"), lx:i%2?"8":"7", bqmc:i%2?"6":"1",
  teacher:["陈老师","李老师","周老师"][i%3], department:"计算机科学技术学院", campus:i%2?"江湾":"邯郸",
  schedule:"周"+(i%5+1)+" 第 3–4 节", location:"教学楼 203", selectable:i!==11, conflict:i===4,
  remaining:i%3===0?0:5, full:i%3===0}));
const goodCheck = () => ({state:"passed", valid:true, checks:[{key:"login", ok:true, message:"验证通过"}, {key:"courses", ok:true, message:"验证通过"}]});
async function main() {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({executablePath:"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless:true});
  const context = await browser.newContext({viewport:{width:1360,height:1000}, deviceScaleFactor:1, reducedMotion:"reduce"});
  const page = await context.newPage();
  const errors=[], unexpected=[];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => dialog.accept());
  let config=structuredClone(initialConfig), savedLogin=false, check={state:"idle",valid:false,checks:[]};
  let task={running:false,action:null,started_at:null,finished_at:null,exit_code:null}, logs="", checksLeft=0;
  let starts=0, savesFail=false, startFail=false, statusFail=false, checkFail=false, checkRequestFail=false;
  let appPlatform="darwin", openedData=false, quitCalled=false;
  await context.route("**/*", async route => {
    const req=route.request(), url=new URL(req.url()), pathname=url.pathname;
    if (url.origin!==origin) {unexpected.push(req.url()); return route.abort();}
    if (!pathname.startsWith("/api/")) return route.continue();
    const reply=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if (pathname==="/api/config") {
      if (req.method()==="PUT") {
        if(savesFail) return reply({error:"模拟：保存失败，请重试"},400);
        config=req.postDataJSON().config; return reply({config});
      }
      return reply({config,exists:true,login:{saved:savedLogin,updated_at:1},app:{version:"1.2.0",platform:appPlatform,desktop:true,data_dir:"/tmp/FDU Test",control_token:"LOCAL-DESKTOP-CONTROL"}});
    }
    if (pathname==="/api/status") {
      if(statusFail) return reply({error:"模拟连接中断"},503);
      if(checksLeft && --checksLeft===0) check=checkFail?{state:"failed",valid:false,checks:[{key:"login",ok:false,message:"登录已失效，请重新导入"}]}:goodCheck();
      const cursor=Number(url.searchParams.get("cursor")||0);
      return reply({...task,output:logs.slice(cursor),cursor:logs.length,preflight:check});
    }
    if(pathname==="/api/cookie") {
      assert.ok(req.postDataJSON().cookie.includes("TEST-ONLY"));
      savedLogin=true; config.cookie_source="file"; check={state:"idle",valid:false,checks:[]};
      return reply({message:"已保存"});
    }
    if(pathname==="/api/courses") return reply({courses:catalog});
    if(pathname==="/api/preflight") {
      if(checkRequestFail) return reply({error:"模拟：自检服务繁忙"},409);
      check={state:"checking",valid:false,checks:[]}; checksLeft=2; return reply(check,202);
    }
    if(pathname==="/api/tasks") {
      assert.equal(req.postDataJSON().require_preflight,true);
      if(startFail) return reply({error:"模拟：启动失败，可重新尝试"},409);
      starts++; task={running:true,action:req.postDataJSON().action,label:"立即抢课",started_at:localTime(Date.now()+starts*1000),finished_at:null,exit_code:null};
      logs="[模拟] 正在提交课程\n课程容量已满\n";
      return reply({...task,output:logs,cursor:logs.length});
    }
    if(pathname==="/api/stop") {
      task={...task,running:false,exit_code:-15,finished_at:localTime(Date.now())}; logs+="任务已停止\n"; return reply({});
    }
    if(pathname==="/api/app/open-data" || pathname==="/api/app/quit") {
      assert.equal(req.headers()["x-fdu-app-token"],"LOCAL-DESKTOP-CONTROL");
      if(pathname.endsWith("open-data")) openedData=true;
      else quitCalled=true;
      return reply({ok:true});
    }
    unexpected.push(pathname); return reply({error:"Unexpected API"},500);
  });
  try {
    await page.goto(origin);
    await page.waitForFunction(() => document.querySelector("#connection-text").textContent==="本地服务已连接");
    await page.screenshot({path:path.join(output,"01-dashboard.png"),fullPage:true});
    await page.click("#next-action-button");
    await page.locator("#cookie-dialog[open]").waitFor();
    await page.click(".login-help summary");
    await page.fill("#cookie-input","Cookie: JSESSIONID=TEST-ONLY; _WEU=TEST-ONLY");
    await page.click("#save-cookie");
    await page.locator("#catalog-list .catalog-row").first().waitFor();
    assert.equal(await page.inputValue("#cookie-input"),"");
    await page.click('[data-category="7:1"]');
    assert.equal(await page.locator(".catalog-row").count(),6);
    await page.click('[data-category=""]');
    await page.fill("#catalog-search","学术英语");
    assert.equal(await page.locator(".catalog-row").count(),2);
    await page.fill("#catalog-search","");
    for(let i=0;i<10;i++) await page.click(`[data-catalog-index="${i}"]`);
    await page.click('[data-catalog-index="10"]');
    assert.ok((await page.locator("#catalog-selection").innerText()).includes("10 / 10"));
    assert.equal(await page.locator('[data-catalog-index="11"]').isDisabled(),true);
    await page.screenshot({path:path.join(output,"02-catalog.png"),fullPage:true});
    savesFail=true; await page.click("#finish-catalog");
    await page.getByText("模拟：保存失败，请重试",{exact:true}).waitFor();
    assert.equal(await page.locator("#catalog-dialog").evaluate(el=>el.open),true);
    savesFail=false; await page.click("#finish-catalog");
    await page.locator('#workflow-dialog[open] [data-workflow-panel="check"]').waitFor();
    await page.waitForFunction(() => document.querySelector("#check-primary").textContent.includes("通过，设置运行"));
    assert.equal(starts,0);
    await page.screenshot({path:path.join(output,"03-check.png"),fullPage:true});
    await page.click("#check-primary");
    assert.ok(!(await page.locator("#run-notice-text").innerText()).includes("未设置"));
    await page.screenshot({path:path.join(output,"04-run.png"),fullPage:true});
    await page.check('input[name="run-mode"][value="now"]');
    startFail=true; await page.click("#start-run");
    await page.waitForFunction(()=>document.querySelector("#run-notice-text").textContent.includes("启动失败"));
    // Error must remain visible after another status poll.
    await page.waitForTimeout(1150);
    assert.ok((await page.locator("#run-notice-text").innerText()).includes("启动失败"));
    startFail=false; await page.click("#start-run");
    await page.waitForFunction(()=>document.querySelector("#monitor-title").textContent.includes("暂无席位"));
    assert.equal(starts,1);
    await page.click("#close-workflow-dialog");
    await page.reload();
    await page.locator('#workflow-dialog[open] [data-workflow-panel="monitor"]').waitFor();
    assert.equal(starts,1);
    assert.equal(await page.locator("#summary-cookie").innerText(),"已验证");
    await page.screenshot({path:path.join(output,"05-monitor.png"),fullPage:true});
    await page.evaluate(()=>window.dispatchEvent(new Event("fdu-close-request")));
    await page.locator("#confirm-dialog[open]").waitFor();
    assert.ok((await page.locator("#dialog-message").innerText()).includes("停止当前任务"));
    await page.click('#confirm-dialog button[value="cancel"]');
    assert.equal(quitCalled,false);
    await page.click("#monitor-stop");
    await page.click('#confirm-dialog button[value="cancel"]');
    assert.equal(task.running,true);
    await page.click("#open-full-log");
    await page.click("#clear-log");
    await page.waitForTimeout(1100);
    assert.ok(!(await page.locator("#terminal-output").innerText()).includes("课程容量已满"));
    logs+="[模拟] 新反馈\n";
    await page.waitForFunction(()=>document.querySelector("#terminal-output").textContent.includes("新反馈"));
    assert.ok(!(await page.locator("#terminal-output").innerText()).includes("课程容量已满"));
    await page.click("#log-stop-task");
    await page.click("#dialog-confirm");
    await page.waitForFunction(()=>document.querySelector("#monitor-title").textContent.includes("已停止"));
    await page.click("#monitor-close");
    // An ended task from another window must fetch its full output even if the old cursor was larger.
    task={...task,action:"now",running:true,started_at:"2026-09-19 10:00:01",finished_at:null};
    logs="[模拟] 第二个窗口的新任务\n";
    await page.waitForFunction(()=>document.querySelector("#terminal-output").textContent.includes("第二个窗口"));
    assert.ok(!(await page.locator("#terminal-output").innerText()).includes("新反馈"));
    task={...task,running:false,exit_code:0,finished_at:"2026-09-19 10:00:02"};
    await page.waitForFunction(()=>document.querySelector("#monitor-title").textContent.includes("本次任务已结束"));
    assert.ok(!(await page.locator("#monitor-title").innerText()).includes("全部选上"));
    await page.click("#monitor-close");
    // Request errors and failed login must stay visible and route back to import.
    check={state:"idle",valid:false,checks:[]};
    await page.click('[data-view="dashboard"]');
    await page.waitForFunction(()=>document.querySelector("#next-action-label").textContent==="运行前自检");
    await page.click("#next-action-button");
    checkRequestFail=true; await page.click("#check-primary");
    await page.waitForFunction(()=>document.querySelector("#check-error").textContent.includes("自检服务繁忙"));
    await page.waitForTimeout(1100);
    assert.equal(await page.locator("#check-error").isVisible(),true);
    checkRequestFail=false; checkFail=true; await page.click("#check-primary");
    await page.waitForFunction(()=>document.querySelector("#check-login-badge").textContent==="需处理");
    await page.click("#check-back");
    await page.locator("#cookie-dialog[open]").waitFor();
    await page.click("#close-cookie-dialog");
    statusFail=true;
    await page.waitForFunction(()=>document.querySelector("#connection-text").textContent.includes("连接中断"));
    assert.equal(await page.locator("#next-action-button").isDisabled(),true);
    statusFail=false; check=goodCheck();
    await page.waitForFunction(()=>document.querySelector("#connection-text").textContent==="本地服务已连接");
    await page.click('[data-view="config"]');
    await page.locator("details.advanced-panel").evaluate(el=>el.open=true);
    assert.equal(await page.locator("[data-static]").isDisabled(),true);
    // Mobile views and every wizard panel must fit the viewport.
    await page.setViewportSize({width:390,height:844});
    await page.click('[data-view="dashboard"]');
    await page.screenshot({path:path.join(output,"06-mobile-dashboard.png"),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.click('[data-flow-step="courses"]');
    await page.locator("#catalog-list .catalog-row").first().waitFor();
    await page.screenshot({path:path.join(output,"07-mobile-catalog.png"),fullPage:true});
    for(const selector of ["#catalog-dialog","#catalog-content"]) assert.equal(await page.locator(selector).evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
    assert.equal(await page.locator("#finish-catalog span").isVisible(),true);
    await page.click("#close-catalog");
    await page.click('[data-flow-step="run"]');
    await page.screenshot({path:path.join(output,"08-mobile-run.png"),fullPage:true});
    assert.equal(await page.locator("#workflow-dialog").evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
    await page.click("#close-workflow-dialog");
    await page.setViewportSize({width:320,height:740});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    appPlatform="win32";
    await page.reload();
    await page.waitForFunction(()=>document.querySelector("#devtools-shortcut").textContent.includes("F12"));
    await page.click("#app-options");
    assert.ok((await page.locator("#app-version").innerText()).includes("Windows"));
    await page.click("#open-data-folder");
    assert.equal(openedData,true);
    await page.click("#quit-app");
    await page.click('#confirm-dialog button[value="cancel"]');
    assert.equal(quitCalled,false);
    await page.click("#quit-app");
    await page.click("#dialog-confirm");
    await page.waitForFunction(()=>document.querySelector("#connection-text").textContent==="应用已退出");
    assert.equal(quitCalled,true);
    assert.deepEqual(errors,[]);
    assert.deepEqual(unexpected,[]);
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    console.log("PASS: full workflow, confirmation, failure/retry, 10-class limit, refresh recovery, cross-window logs, offline state and mobile layout. No external requests.");
    console.log("Screenshots: "+output);
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
