import {test,expect} from '@playwright/test';

test('MiMo preset appears in model management and chat selection', async ({page}) => {
  await page.goto('/');
  await page.getByRole('menuitem',{name:'模型连接'}).click();
  const card = page.locator('.resource-card').filter({has:page.getByRole('heading',{name:'小米 MiMo',exact:true})});
  await expect(card).toContainText('mimo-v2.6-flash');
  await expect(card).toContainText('https://api.xiaomimimo.com/v1');
  await page.getByRole('menuitem',{name:'对话工作台'}).click();
  await page.locator('.conversation-toolbar .ant-select').click();
  await page.locator('.ant-select-item-option').filter({hasText:'小米 MiMo'}).click();
  await expect(page.locator('.conversation-toolbar')).toContainText('小米 MiMo');
});
