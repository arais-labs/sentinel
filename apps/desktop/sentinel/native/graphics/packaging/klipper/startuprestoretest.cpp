/* SPDX-License-Identifier: GPL-2.0-or-later */
// Link to the production klipper target. This test does not mock SystemClipboard
// or duplicate HistoryModel::loadHistory. It inspects the resulting MIME data.
#include "../historyitem.h"
#include "../historymodel.h"

#include <KConfigGroup>
#include <KSharedConfig>
#include <KSystemClipboard>

#include <QApplication>
#include <QClipboard>
#include <QDir>
#include <QMimeData>
#include <QTemporaryDir>
#include <QTest>
#include <utility>

class StartupRestoreTest : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void startupIsConditionalButExplicitSelectionIsNot();
};

void StartupRestoreTest::startupIsConditionalButExplicitSelectionIsNot()
{
    auto config = KSharedConfig::openConfig(QStringLiteral("klipperrc"), KConfig::NoGlobals);
    KConfigGroup general(config, QStringLiteral("General"));
    general.writeEntry("KeepClipboardContents", true);
    // Isolate startup restoration from the separate preserve-empty callback.
    general.writeEntry("NoEmptyClipboard", false);
    general.writeEntry("MaxClipItems", 10);
    QVERIFY(config->sync());

    const QString first = QStringLiteral("older explicit history selection");
    const QString latest = QStringLiteral("saved startup selection");
    const QString marker = QStringLiteral("application/x-kde-onlyReplaceEmpty");
    QString firstUuid;
    auto clipboard = KSystemClipboard::instance();
    auto hasText = [clipboard](const QString &text) {
        const QMimeData *mime = clipboard->mimeData(QClipboard::Clipboard);
        return mime && mime->text() == text;
    };
    auto hasMarker = [clipboard, &marker] {
        const QMimeData *mime = clipboard->mimeData(QClipboard::Clipboard);
        return mime && mime->hasFormat(marker);
    };

    // Persist two real database records and let publication finish before
    // destroying the model. No fabricated SQLite rows or completion timers.
    {
        auto model = HistoryModel::self();
        QCOMPARE(model->rowCount(), 0);
        QVERIFY(model->insert(first));
        QTRY_COMPARE(model->pendingJobs(), 0);
        firstUuid = model->first()->uuid();
        QVERIFY(model->insert(latest));
        QTRY_COMPARE(model->pendingJobs(), 0);
        const QString latestUuid = model->first()->uuid();
        // Select a different row: older Plasma versions deliberately ignore
        // moveToTop(0), rather than republishing the current first item.
        model->moveToTop(firstUuid);
        QTRY_VERIFY(hasText(first));
        model->moveToTop(latestUuid);
        QTRY_VERIFY(hasText(latest));
        QCOMPARE(model->rowCount(), 2);
    }

    // With no live HistoryModel, clear cannot trigger its preserve-empty path.
    clipboard->clear(QClipboard::Clipboard);
    if (QGuiApplication::clipboard()->supportsSelection()) {
        clipboard->clear(QClipboard::Selection);
    }
    QTRY_VERIFY(!hasText(latest));

    // A new production HistoryModel loads the real saved history and publishes
    // it through the real SystemClipboard/DatabaseRecordToMimeDataJob path.
    auto model = HistoryModel::self();
    QCOMPARE(model->rowCount(), 2);
    QTRY_VERIFY(hasText(latest));
    QVERIFY(hasMarker()); // Negative control: unpatched loadHistory fails here.

    // The actual history-selection entry point must still replace live content.
    model->moveToTop(firstUuid);
    QTRY_VERIFY(hasText(first));
    QVERIFY(!hasMarker());
    QCOMPARE(model->rowCount(), 2);
    QTRY_COMPARE(model->pendingJobs(), 0);
}

int main(int argc, char **argv)
{
    QTemporaryDir state;
    if (!state.isValid()) {
        return 1;
    }
    // Set paths before Qt/KConfig initialization; never touch normal history.
    for (const auto &entry : {std::pair{"XDG_DATA_HOME", "data"},
                              std::pair{"XDG_CONFIG_HOME", "config"},
                              std::pair{"XDG_CACHE_HOME", "cache"}}) {
        const QString directory = state.path() + QLatin1Char('/') + QLatin1String(entry.second);
        if (!QDir().mkpath(directory)) {
            return 1;
        }
        qputenv(entry.first, directory.toUtf8());
    }
    QApplication application(argc, argv);
    StartupRestoreTest test;
    return QTest::qExec(&test, argc, argv);
}

#include "startuprestoretest.moc"
