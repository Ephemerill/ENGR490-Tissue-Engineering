Sudo su
cd /Desktop

echo “updates /n”
sudo apt update && sudo apt upgrade -y

# automatic updates 
apt install unattended-upgrades 
dpkg-reconfigure -plow unattended-upgrades


# firewall and logs
echo “firewall /n”
apt install fail2ban

apt install ufw 
ufw allow 2244/tcp
ufw allow 80/tcp
ufw enable 


# install tailscale 
echo “tailscale /n”
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
ip addr show tailscale0
sudo ufw allow in on tailscale0

# git 
echo “GIT /n”
apt install git 
Git clone https://github.com/Ephemerill/ENGR490-Tissue-Engineering.git

# ssh
sudo apt install openssh-server
sudo systemctl start ssh
sudo systemctl enable ssh
