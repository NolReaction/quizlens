# Выпуск QuizLens для macOS

Для распространения вне Mac App Store нужен сертификат **Developer ID Application** с закрытым ключом и доступ к нотариальному сервису Apple. Сертификат создаёт уполномоченный участник команды Apple Developer Program. Подпись указывает издателя, а принятая нотарификация подтверждает проверку Apple для конкретной сборки. macOS может показать обычный диалог первого открытия приложения из интернета и отдельно спросить разрешение на захват окна. [Developer ID](https://developer.apple.com/developer-id/) · [Нотарификация macOS](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

## Первый выпуск с собственного Mac

1. Установите Xcode с SDK macOS 14 или новее, проверьте работу Command Line Tools и Python 3.9+. В Связке ключей должны быть сертификат Developer ID Application, его закрытый ключ и соответствующий промежуточный сертификат Apple.
2. В Terminal проверьте доступные сертификаты и задайте профиль нотарификации. В качестве имени сертификата используйте полную строку из вывода первой команды. Команда для профиля запрашивает данные Apple интерактивно. Инструмент поддерживает также аутентификацию ключом App Store Connect. [Настройка notarytool](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).

   ```bash
   security find-identity -v -p codesigning
   xcrun notarytool store-credentials QuizLensNotary
   ```

3. Из корня репозитория запустите сборку. Замените пример имени своей учётной записью разработчика. Если профиль и сертификат находятся в отдельной связке, укажите её путь в переменной `QUIZLENS_KEYCHAIN`.

   ```bash
   export QUIZLENS_SIGN_IDENTITY='Developer ID Application: Имя разработчика (TEAMID)'
   export QUIZLENS_NOTARY_PROFILE='QuizLensNotary'
   python3 packaging/package_mac.py --notarize --output dist/QuizLens-Mac.zip
   ```

4. Отдайте пользователю ZIP после успешного завершения команды. Закрытый ключ и пароль остаются на машине подписи. Настройки релиза через GitHub Actions описаны в [памятке для владельца репозитория](PUBLISH.md).

## Что проверяет сборка

Сборщик компилирует программу запуска и три вспомогательные утилиты в формате Universal для Apple Silicon и Intel. Утилиты распознавания, чтения PDF и захвата окна помещаются в `Contents/Helpers`. Сначала подпись с Hardened Runtime и временной меткой получают утилиты, затем всё приложение. Сборщик проверяет обе архитектуры, отправляет временный ZIP в Apple и ждёт статуса `Accepted`.

После проверки Apple сборщик прикрепляет квитанцию к приложению через `stapler`. В финальный ZIP приложение попадает с помощью `ditto`, которое сохраняет метаданные macOS. Архив повторно распаковывается, после чего сборщик проверяет подпись, квитанцию и оценку Gatekeeper. При ошибке предыдущий выходной архив сохраняется. На компьютере пользователя эта версия запускает готовые утилиты. Для сервера приложения требуется установленный Python 3.9+.

Если сервис Apple вернул `Invalid`, прочтите журнал по идентификатору запроса, который напечатал сборщик. После исправления нужно подписать и отправить новую сборку.

```bash
xcrun notarytool log ID_ЗАПРОСА --keychain-profile QuizLensNotary
```

## Проверка на другом компьютере

Скачайте опубликованный ZIP браузером на другой Mac, распакуйте в Finder, перетащите QuizLens.app в «Программы» и откройте двойным щелчком. В строке меню появится **QuizLens**. Через это меню можно открыть страницу приложения или завершить его работу. При необходимости отдельно проверьте квитанцию и источник подписи.

```bash
xcrun stapler validate /Applications/QuizLens.app
spctl --assess --type execute --verbose=4 /Applications/QuizLens.app
codesign --verify --deep --strict --all-architectures /Applications/QuizLens.app
```

## Сборка для разработки

Команда `python3 packaging/package_mac.py --output dist/QuizLens-Mac-dev.zip` на Mac создаёт вариант с локальной подписью ad hoc. При скачивании macOS может заблокировать его из-за неподтверждённого издателя. Для локальной разработки из клона репозитория можно запустить `bash launch.command` с установленными инструментами Apple.

При запуске упаковщика в Linux получается архив исходного приложения для Mac с `Start.command` и сборкой утилит при первом запуске через Apple Command Line Tools. Для публикации Universal-приложения со встроенными утилитами используйте runner macOS.
