# Как опубликовать QuizLens на GitHub

Репозиторий хранит исходники, GitHub Releases раздаёт архивы, а GitHub Pages даёт одну ссылку для пользователей Mac и Windows. Можно разместить страницу до первого выпуска. Архивы появятся после успешной сборки выпуска.

## 1. Перенести проект в свой репозиторий

Для первой публикации создай на GitHub **пустой публичный репозиторий**, если хочешь, чтобы друзья могли скачивать сборки без приглашения. Имя можно выбрать любое. Скопируй HTTPS-ссылку из **Code** или **Quick setup**. В примерах ниже замени `OWNER/REPO` своими значениями.

**Если у тебя файл QuizLens-Git.bundle.** Сохрани его в текущей папке и склонируй в новый каталог через Terminal.

```bash
git clone --branch main ./QuizLens-Git.bundle QuizLens
cd QuizLens
git remote set-url origin https://github.com/OWNER/REPO.git
git log -1 --oneline
git tag --list v1.5.0
git push -u origin main
```

Bundle содержит Git-историю, ветку `main` и тег `v1.5.0`. После клонирования `origin` сначала указывает на локальный файл. Команда `set-url` заменяет его адресом созданного репозитория. На этом шаге отправляется только ветка. [Как работает git bundle](https://git-scm.com/docs/git-bundle).

**Если проект уже находится на GitHub.** Склонируй его обычной командой `git clone https://github.com/OWNER/REPO.git`, перейди в папку проекта и продолжай с настройки Pages.

**Если в новом репозитории уже есть README или другой коммит.** Чтобы сохранить обе истории, клонируй существующий репозиторий и импортируй подготовленную ветку. Здесь предполагается, что bundle лежит рядом с каталогом клона, а основная ветка удалённого репозитория называется `main`.

```bash
git clone https://github.com/OWNER/REPO.git QuizLens
cd QuizLens
git switch main
git fetch --no-tags ../QuizLens-Git.bundle refs/heads/main:refs/remotes/bundle/main
git merge --allow-unrelated-histories --no-ff bundle/main
```

Если конфликт возник в README, сохрани описание QuizLens и нужные строки старого README, выполни `git add README.md` и `git commit`. После проверки опубликуй объединённую ветку командой `git push -u origin main`. Для этой ветки тег будет создан на шаге 3.

## 2. Включить страницу загрузки

1. В GitHub открой **Settings → Pages → Build and deployment**. Для нового репозитория выбери **Source → GitHub Actions**. QuizLens также поддерживает существующую настройку **Deploy from a branch → gh-pages → /(root)**. В этом режиме процесс обновляет четыре файла страницы в ветке `gh-pages`, сохраняет её историю и запрашивает сборку GitHub Pages. Для настройки нужны права администратора и доступ к Actions.
2. Во вкладке **Actions** выбери процесс **Download page** (`pages.yml`). Если Pages ещё выключен, процесс покажет подсказку. После настройки нажми **Run workflow**, выбери `main` и запусти процесс. Последующие обновления `main` публикуются автоматически.
3. После успешного развёртывания открой **Settings → Pages → Visit site**. Этот URL можно пересылать друзьям. Для обычного репозитория адрес выглядит как `https://OWNER.github.io/REPO/`. Для репозитория `OWNER.github.io` или собственного домена используй фактический URL из настроек. Якорь `#start` ведёт к первым шагам.
4. На главной странице репозитория нажми шестерёнку у **About** и помести URL страницы в поле **Website**. Тогда ссылка останется на виду рядом с исходниками. [Настройка GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

## 3. Выпустить архивы

**Для Mac выбери режим выпуска до отправки тега.** Если нужно распространение с Developer ID и нотарификацией Apple, настрой учётные данные по отдельной инструкции. Без них workflow публикует тестовую Mac-сборку с явной отметкой об отсутствии нотарификации. Если настроена только часть учётных данных или проверка подписи завершается ошибкой, выпуск прерывается. [Подпись и нотарификация](MAC-SIGNING.md).

Убедись, что проверка ветки `main` в **Actions** прошла. Версия из `packaging/VERSION` должна совпадать с тегом без начальной буквы `v`. В первом выпуске это `1.5.0`.

```bash
git switch main
cat packaging/VERSION
git tag --list v1.5.0
```

В клоне из bundle тег уже существует. Если вывод команды пуст, создай тег на проверенном коммите.

```bash
git tag -a v1.5.0 -m "QuizLens 1.5.0"
```

Отправка одного тега запускает сборку выпуска.

```bash
git push origin v1.5.0
```

Процесс **Publish release** (`release.yml`) проверит тег, соберёт приложения и создаст выпуск. В **Releases → Latest → Assets** появятся `QuizLens-Mac.zip`, `QuizLens-Windows.zip`, `QuizLens-Extension.zip`, `QuizLens-Guide.md`, `SHA256SUMS.txt` и `release.json`. Манифест содержит статус Mac-сборки. Открой страницу загрузки, проверь кнопки Mac, Windows и расширения и статус. Для дальнейших обновлений измени `packaging/VERSION`, опубликуй коммит и отправь соответствующий **новый** тег.

**Выпуск через ветку.** Если удобнее работать через интерфейс GitHub, открой меню веток над списком файлов на `main`, введи `release/v1.5.0` и создай эту ветку из проверенного коммита. Через Git ту же операцию выполняет команда ниже. Используй один из способов запуска выпуска.

```bash
git push origin main:refs/heads/release/v1.5.0
```

Суффикс ветки должен совпадать с версией в `packaging/VERSION` с префиксом `v`. После прохождения тестов и проверки обоих архивов процесс создаст тег на собранном коммите и опубликует выпуск. Если тег уже указывает на тот же коммит, повторный запуск продолжит публикацию. Если тег указывает на другой коммит, процесс остановится. Для изменённого кода используй новую версию. Ход сборки виден в **Actions → Publish release**.

**Постоянные ссылки.** Для друзей используй адрес Pages. Для ссылки прямо на последнюю сборку можно добавить к адресу GitHub-репозитория `/releases/latest/download/QuizLens-Mac.zip` для Mac, `/releases/latest/download/QuizLens-Windows.zip` для Windows или `/releases/latest/download/QuizLens-Extension.zip` для расширения. На Pages также доступна копия `./QuizLens-Extension.zip`, которая загружается при недоступном API GitHub. Список всех файлов откроется по `/releases/latest`. [Ссылки на выпуски GitHub](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases).

**Проверка скачанного файла.** Скачай из одного выпуска нужный архив и `SHA256SUMS.txt` и сравни SHA-256 архива со строкой для его имени. На Mac используй `shasum -a 256 QuizLens-Mac.zip`, в Windows PowerShell — `Get-FileHash .\QuizLens-Windows.zip -Algorithm SHA256`. Для статуса Gatekeeper смотри отметку о нотарификации Mac.

## 4. Проверять изменения и собирать локально

Команды ниже выполняются из корня проекта в shell на Mac. Для проверки Python-модулей используй Python 3.9 или новее, для интерфейсных тестов — актуальный Node.js LTS. Тесты чтения изображений и PDF дополнительно используют Pillow, pypdf, Tesseract, Apple Vision или `pdftotext` в зависимости от платформы.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install Pillow pypdf
python -m unittest discover -s tests
node --test tests/*.cjs
```

Для разработки текстовых сценариев запусти `python app.py`. Для полной разработки нативного захвата на macOS нужны Apple Command Line Tools. Штатный запуск из исходников — `./launch.command`. Демонстрационные карточки и подбор модели можно проверить без установки весов модели. Для сквозной проверки с моделью включи локальный сервер Bionic или LM Studio.

```bash
python packaging/package_extension.py --output dist/QuizLens-Extension.zip
python packaging/package_windows.py --output dist/QuizLens-Windows.zip
python packaging/package_mac.py --output dist/QuizLens-Mac.zip
```

Для выпуска готовых нативных модулей Mac запускай Mac-сборку на macOS с инструментами сборки Apple. Команда без параметров подписи создаёт вариант для разработки. Порядок выпуска с нотарификацией приведён в [инструкции по подписи](MAC-SIGNING.md).
