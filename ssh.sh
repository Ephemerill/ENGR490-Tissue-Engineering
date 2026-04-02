echo “ssh /n”
# sshd 
echo “PasswordAuthentication no
PermitRootLogin no
AllowUsers [change user before running]
Port 2244
MaxAuthTries 3” >> /etc/ssh/sshd_config

# ssh
echo “Host printer
    HostName [change to Pi IP]
    User your_username              
    Port 2244                       
    IdentityFile ~/.ssh/id_ed25519  # Path to your private SSH key
    IdentitiesOnly yes              # Only use the key specified above
    ServerAliveInterval 60          # Keeps the connection from "hanging"
    ServerAliveCountMax 3” >> /etc/ssh/ssh_config
