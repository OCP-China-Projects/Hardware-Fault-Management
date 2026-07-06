#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(hfm, LOG_LEVEL_INF);

#define HFM_HEARTBEAT_INTERVAL K_SECONDS(1)

int main(void)
{
    printk("HFM app boot on %s\n", CONFIG_BOARD);
    LOG_INF("hfm main thread started");

    uint32_t tick = 0;
    while (1) {
        LOG_INF("heartbeat #%u", tick++);
        k_sleep(HFM_HEARTBEAT_INTERVAL);
    }
    return 0;
}
