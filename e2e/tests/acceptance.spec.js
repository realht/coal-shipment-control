const { test, expect } = require('@playwright/test');
const { expectAccessible, fixture, installFailureGuards, login } = require('./helpers');

test.beforeEach(async ({ page }) => {
  page.verifyNoBrowserFailures = installFailureGuards(page);
});

test.afterEach(async ({ page }) => page.verifyNoBrowserFailures());

test('login, invalid login, direct URL permissions and keyboard accessibility', async ({ page }) => {
  await page.goto('/accounts/login/');
  await page.locator('[name=username]').fill('e2e-admin');
  await page.locator('[name=password]').fill('wrong-password');
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page).toHaveURL(/accounts\/login/);

  await login(page, 'e2e-viewer', 'Acceptance-Viewer-2026');
  const listResponse = await page.goto('/auto/');
  expect(listResponse && listResponse.ok()).toBeTruthy();
  await page.goto('/auto/new/');
  await expect(page.locator('body')).toContainText('403');
  await page.goto('/settings/system/');
  await expect(page.locator('body')).toContainText('403');

  await page.goto('/accounts/login/');
  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toBeVisible();
  await expectAccessible(page);
});

test('filters, pagination and partial selection persist across pages', async ({ page }) => {
  await login(page);
  await page.goto('/auto/?q=E2E');
  await expect(page.locator('tbody tr')).toHaveCount(25);
  await page.locator('#partial-export-toggle').click();
  await page.locator('.row-checkbox').first().check();
  await page.getByRole('link', { name: '2', exact: true }).click();
  await page.locator('.row-checkbox').first().check();
  await expect(page.locator('#export-selected-label')).toContainText('2');
  await expectAccessible(page);
});

test('import preview and confirm creates an import log result', async ({ page, browserName }) => {
  await login(page);
  await page.goto('/imports/upload/?type=auto');
  await page.locator('[name=year]').fill('2026');
  // Фикстура уникальна на project (см. prepare.py): оба браузера импортируют в общую БД в одном
  // прогоне, поэтому единый файл во втором проекте стал бы дублем и импорт нечего было бы выбрать.
  await page.locator('[name=excel_file]').setInputFiles(fixture(`auto-import-${browserName}.xlsx`));
  await page.getByRole('button', { name: /Загрузить|Продолжить/ }).click();
  await expect(page).toHaveURL(/imports\/preview/);
  await expect(page.locator('body')).toContainText('E2E импортированный объект');
  await page.getByRole('button', { name: 'Импортировать выбранные' }).click();
  await expect(page).toHaveURL(/imports\/result\/\d+/);
  await expect(page.locator('body')).toContainText(/успеш|Импорт выполнен/i);
});

test('document upload, edit, download and delete', async ({ page }) => {
  await login(page);
  await page.goto('/auto/');
  await page.locator('tbody a[href^="/auto/"]').first().click();
  await page.getByRole('link', { name: 'Прикрепить' }).click();
  await page.locator('[name=file]').setInputFiles(fixture('document.png'));
  await page.getByRole('button', { name: 'Загрузить' }).click();
  await expect(page.locator('body')).toContainText('document.png');
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'document.png' }).click();
  await download;
  await page.getByRole('link', { name: /Изменить/ }).click();
  await page.locator('[name=document_type]').selectOption({ index: 1 });
  await page.getByRole('button', { name: /Сохранить/ }).click();
  await page.getByRole('link', { name: 'Удалить', exact: true }).last().click();
  await page.getByRole('button', { name: 'Удалить' }).click();
  await expect(page.locator('body')).not.toContainText('document.png');
});

test('catalog values and system status/backup queue are operable', async ({ page, browserName }) => {
  await login(page);
  await page.goto('/settings/catalogs/');
  const toggle = page.getByTitle('Включить справочник').first();
  if (await toggle.count()) await toggle.click();
  await page.getByRole('link', { name: /Значения/ }).first().click();
  // Значение уникально на project: оба браузера пишут в общую БД в одном прогоне, а второй проект
  // на общем имени добавил бы дубль и деактивировал бы уже деактивированное первым проектом значение.
  const catalogValue = `E2E значение ${browserName}`;
  await page.locator('[name=name]').fill(catalogValue);
  await page.getByRole('button', { name: /Добавить/ }).click();
  await expect(page.locator('body')).toContainText(catalogValue);
  await page.getByRole('button', { name: /Деактивировать/ }).last().click();

  await page.goto('/settings/system/');
  await expect(page.getByRole('heading', { name: 'Система и резервные копии' })).toBeVisible();
  await expectAccessible(page);
  await page.locator('[name=reason]').fill('E2E acceptance');
  await page.getByRole('button', { name: 'Включить профилактику' }).click();
  await page.locator('[name=comment]').fill('E2E acceptance');
  await page.getByRole('button', { name: 'Full backup' }).click();
  await expect(page.locator('body')).toContainText(/очеред|запущен/i);

  // Оба browser-project прогоняются в одном `playwright test` против общей БД, а scheduler в e2e
  // не запущен, поэтому тест убирает за собой обе оставленные мутации, иначе второй проект стартует
  // на грязном состоянии: 1) сбрасываем зависшую backup-операцию (иначе active_operation держит
  // «Full backup» disabled и второй проект таймаутит), 2) возвращаем normal (иначе viewer в тесте
  // логина ловит 503 на странице профилактики).
  await page.getByRole('button', { name: 'Сбросить зависшие операции' }).click();
  await page.getByRole('button', { name: 'Вернуть normal' }).click();
  await expect(page.getByRole('button', { name: 'Включить профилактику' })).toBeEnabled();
});
