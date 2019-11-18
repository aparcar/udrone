# Udrone - Setup Guide

* Where do I get udrone
* How do I setup udrone software
* How do I setup udrone hardware
* How do I run a test

## Where do I get udrone

Checkout the latest version via the following command

```
git clone https://github.com/blogic/udrone
```

## How do I setup udrone software

udrone is very easy to setup. All custom information is stored in a central
file called conf.py

```
$ cat conf.py
conf = {
    # the interface used to talk to the drones
    ifname":"eth0",
}
```

## How do I setup udrone hardware

In addition to the DUT you will need N drones. A drone is a OpenWrt router with
the udrone package installed. Once you have all devices, you need to set them
up in 1 of the following ways.

```
LAPTOP (eth0) -> (LAN) DRONE (WAN) -> (LAN) DUT (WAN) -> BACKEND
LAPTOP (eth0) -> (LAN) DRONE (WIFI) -> (WIFI) DUT (WAN) -> BACKEND
```

If you want to use more than just 1 Drone you will need to switches between
LAPTOP/DRONE and DRONE/DUT.

As a backend you will need, depending on the test: AP/PPPoE Server/DHCP/DNS/...

## How do I run a test

The simplest test is the connectivity test. This will make the drone grab an
IPV4 using DHCP from the DUT and wget http://openwrt.org/index.html.

```
$ ./qa.py test/example.connectivity.json
```
