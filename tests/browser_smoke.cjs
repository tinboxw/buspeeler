// Run against a started local application. Test data goes only to its test data directory.
const fs = require('node:fs');
const path = require('node:path');
const temporary = path.join(path.dirname(process.env.BUSPEELER_INSTANCE), 'browser-temp');
fs.mkdirSync(temporary, {recursive:true});
process.env.TEMP=temporary; process.env.TMP=temporary;
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async()=>{
  const info=JSON.parse(fs.readFileSync(process.env.BUSPEELER_INSTANCE,'utf8'));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1600,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto(info.url);
    await page.getByRole('heading',{name:'从原始数据到可追溯协议'}).waitFor();
    await page.getByRole('button',{name:'创建项目',exact:true}).click();
    await page.getByRole('button',{name:'载入合成演示',exact:true}).waitFor({state:'visible'});
    await page.getByRole('button',{name:'载入合成演示',exact:true}).click();
    await page.getByText('合成演示数据，不属于实车验证证据',{exact:true}).waitFor();
    await page.getByRole('button',{name:'统计特征',exact:true}).click();
    await page.getByRole('heading',{name:'位翻转率 · bit 0 为字节最低位'}).waitFor({timeout:60000});
    await page.getByRole('button',{name:'搜索位边界候选',exact:true}).click();
    await page.getByRole('button',{name:'建立候选',exact:true}).first().waitFor({timeout:60000});
    await page.screenshot({path:process.env.BUSPEELER_SCREENSHOT,fullPage:true});
    await page.getByRole('button',{name:'新建候选',exact:true}).click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.getByRole('navigation').getByRole('button',{name:'协议',exact:true}).click();
    await page.getByRole('heading',{name:'信号定义',exact:true}).waitFor();
    await page.locator('.el-table__body-wrapper .el-checkbox').first().click();
    const downloadPromise=page.waitForEvent('download');
    await page.getByRole('button',{name:'导出实验包',exact:true}).click();
    const download=await downloadPromise; if(!download.suggestedFilename().endsWith('.zip'))throw Error('Missing ZIP');
    await page.getByRole('button',{name:'正式发布',exact:true}).click();
    await page.getByText('正式导出仅接受当前适用范围内独立验证通过的候选',{exact:true}).first().waitFor();
    await page.reload();
    await page.getByRole('navigation').getByRole('button',{name:'数据',exact:true}).click();
    await page.getByRole('heading',{name:'采集会话',exact:true}).waitFor();
    if(errors.length)throw Error(errors.join('\n'));
    console.log('Browser smoke passed: project, demo, plots, process jobs, export and release rejection, reload persistence.');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
